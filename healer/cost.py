"""LLM cost and latency instrumentation, spend ceiling, and circuit breaker.

The Healer calls a model once per tier-2/tier-3 ticket. Before this module
there was no token accounting, no ceiling, and no breaker: a provider outage
that failed every request, or an error storm that produced thousands of
tickets, translated straight into spend nobody could see until the invoice.

Two independent guards:

Budget
    A rolling window (default 24h) over recorded usage. The window is enforced
    on tokens always, and additionally on US dollars when a price is configured
    for the model. If a dollar ceiling is set but the model's price is unknown,
    proposals are refused rather than run uncosted — a ceiling that silently
    does not apply is worse than no ceiling.

Breaker
    N consecutive failed LLM attempts opens the circuit for a cooldown period.
    Retrying a provider that is down, once per queued ticket, at full prompt
    size, is the expensive failure mode.

Both read from the ticket database, so the sidecar and the CLI see the same
accounting rather than each keeping a private counter.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

TOKEN_BUDGET_ENV = "SPECORA_HEALER_TOKEN_BUDGET"
SPEND_LIMIT_ENV = "SPECORA_HEALER_SPEND_LIMIT_USD"
BUDGET_WINDOW_ENV = "SPECORA_HEALER_BUDGET_WINDOW_HOURS"
MODEL_PRICES_ENV = "SPECORA_HEALER_MODEL_PRICES"
BREAKER_THRESHOLD_ENV = "SPECORA_HEALER_BREAKER_FAILURE_THRESHOLD"
BREAKER_COOLDOWN_ENV = "SPECORA_HEALER_BREAKER_COOLDOWN_SECONDS"

DEFAULT_TOKEN_BUDGET = 2_000_000
DEFAULT_WINDOW_HOURS = 24.0
DEFAULT_BREAKER_THRESHOLD = 5
DEFAULT_BREAKER_COOLDOWN_SECONDS = 300.0


@dataclass
class LLMUsage:
    """What one LLM attempt cost, in tokens, time, and money."""

    model_id: str = ""
    provider: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = True
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "prompt_version": self.prompt_version,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "ok": self.ok,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LLMUsage:
        return cls(
            model_id=d.get("model_id", ""),
            provider=d.get("provider", ""),
            prompt_version=d.get("prompt_version", ""),
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            latency_ms=int(d.get("latency_ms", 0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
            ok=bool(d.get("ok", True)),
            error=d.get("error", ""),
        )


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str = ""


class UsageStore(Protocol):
    """The persistence the governor needs. Implemented by :class:`HealerQueue`."""

    def record_llm_usage(self, ticket_id: str, usage: LLMUsage) -> None: ...

    def llm_usage_totals_since(self, cutoff: datetime) -> dict: ...

    def recent_llm_outcomes(self, limit: int) -> list[tuple[bool, datetime]]: ...


@dataclass
class ModelPrice:
    """USD per one million tokens."""

    input_per_mtok: float
    output_per_mtok: float


def load_model_prices() -> dict[str, ModelPrice]:
    """Read the price table from the environment.

    No prices are hardcoded: published rates change and a stale table would
    under-report spend while looking authoritative.
    """
    raw = os.environ.get(MODEL_PRICES_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("%s is not valid JSON; no prices loaded", MODEL_PRICES_ENV)
        return {}
    prices: dict[str, ModelPrice] = {}
    for model_id, entry in parsed.items():
        try:
            prices[model_id] = ModelPrice(
                input_per_mtok=float(entry["input"]),
                output_per_mtok=float(entry["output"]),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Ignoring malformed price entry for %s in %s", model_id, MODEL_PRICES_ENV
            )
    return prices


def estimate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    prices: Optional[dict[str, ModelPrice]] = None,
) -> Optional[float]:
    """Cost of one call, or None when the model has no configured price."""
    table = load_model_prices() if prices is None else prices
    price = table.get(model_id)
    if price is None:
        return None
    return (
        input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok
    ) / 1_000_000.0


@dataclass
class SpendGovernor:
    """Gates LLM calls on a rolling budget and a consecutive-failure breaker."""

    store: UsageStore
    prices: dict[str, ModelPrice] = field(default_factory=load_model_prices)

    @property
    def window(self) -> timedelta:
        return timedelta(hours=_float_env(BUDGET_WINDOW_ENV, DEFAULT_WINDOW_HOURS))

    @property
    def token_budget(self) -> int:
        return int(_float_env(TOKEN_BUDGET_ENV, float(DEFAULT_TOKEN_BUDGET)))

    @property
    def spend_limit_usd(self) -> Optional[float]:
        raw = os.environ.get(SPEND_LIMIT_ENV, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            logger.warning("%s is not a number (%r); ignoring", SPEND_LIMIT_ENV, raw)
            return None

    def check(self, model_id: str = "") -> BudgetDecision:
        """Decide whether an LLM call may proceed right now."""
        breaker = self._breaker_decision()
        if not breaker.allowed:
            return breaker

        cutoff = datetime.now(timezone.utc) - self.window
        totals = self.store.llm_usage_totals_since(cutoff)
        spent_tokens = int(totals.get("total_tokens", 0))
        budget = self.token_budget
        if budget > 0 and spent_tokens >= budget:
            return BudgetDecision(
                False,
                f"Token budget exhausted: {spent_tokens} of {budget} tokens used "
                f"in the last {self.window.total_seconds() / 3600:.0f}h.",
            )

        limit = self.spend_limit_usd
        if limit is not None:
            if model_id and model_id not in self.prices:
                return BudgetDecision(
                    False,
                    f"{SPEND_LIMIT_ENV} is set but no price is configured for "
                    f"model '{model_id}'. Add it to {MODEL_PRICES_ENV}.",
                )
            spent_usd = float(totals.get("total_cost_usd", 0.0))
            if spent_usd >= limit:
                return BudgetDecision(
                    False,
                    f"Spend ceiling reached: ${spent_usd:.4f} of ${limit:.2f} "
                    f"in the last {self.window.total_seconds() / 3600:.0f}h.",
                )
        return BudgetDecision(True)

    def record(self, ticket_id: str, usage: LLMUsage) -> None:
        self.store.record_llm_usage(ticket_id, usage)

    def price_call(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        return estimate_cost_usd(model_id, input_tokens, output_tokens, self.prices) or 0.0

    def _breaker_decision(self) -> BudgetDecision:
        threshold = int(_float_env(BREAKER_THRESHOLD_ENV, float(DEFAULT_BREAKER_THRESHOLD)))
        if threshold <= 0:
            return BudgetDecision(True)
        cooldown = _float_env(BREAKER_COOLDOWN_ENV, DEFAULT_BREAKER_COOLDOWN_SECONDS)
        outcomes = self.store.recent_llm_outcomes(threshold)
        if len(outcomes) < threshold or any(ok for ok, _ in outcomes):
            return BudgetDecision(True)
        newest_failure = max(ts for _, ts in outcomes)
        age = (datetime.now(timezone.utc) - newest_failure).total_seconds()
        if age >= cooldown:
            return BudgetDecision(True)
        return BudgetDecision(
            False,
            f"Circuit breaker open: {threshold} consecutive LLM failures; "
            f"retrying in {cooldown - age:.0f}s.",
        )


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s is not a number (%r); using default", name, raw)
        return default

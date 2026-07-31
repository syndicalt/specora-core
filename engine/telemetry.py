"""Per-call LLM instrumentation — tokens, latency, outcome, and spend gating.

Two collaborating extension points live here:

``TelemetrySink``
    Receives a :class:`CallRecord` *after* every engine call, successful or
    not. Sinks are observers; a sink that raises is logged and skipped so a
    broken metrics backend can never take down a healing run.

``CallGate``
    Consulted *before* every engine call. A gate raises
    :class:`CallBlockedError` to refuse the call. This is the hook a spend
    ceiling uses: the healer registers a gate that sums the records it has
    seen and refuses once the budget is spent.

Cost is deliberately not hardcoded. Published token prices drift and a stale
constant compiled into the engine would silently misreport spend, which is
worse than reporting none. Token counts are ground truth and always recorded;
dollars are derived only when pricing has been supplied via the
``SPECORA_MODEL_PRICING`` environment variable or
:func:`set_model_pricing`. :meth:`UsageAggregator.totals` reports how many
calls it could not price so the gap is visible rather than assumed to be zero.

Usage::

    from engine import telemetry

    agg = telemetry.get_default_aggregator()
    ...                                   # run engine calls
    print(agg.totals())
    # {"calls": 3, "input_tokens": 4120, "output_tokens": 388,
    #  "estimated_cost_usd": None, "unpriced_calls": 3, ...}
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class CallBlockedError(Exception):
    """Raised by a :class:`CallGate` to refuse an LLM call before it is sent.

    Deliberately not a transient error: the retry layer must never retry
    around a refusal, or a spend ceiling would be spent three times over.
    """


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens, as supplied by the operator."""

    input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True)
class CallRecord:
    """One completed engine call — the unit the Healer aggregates."""

    model: str
    provider: str
    purpose: str
    outcome: str  # "ok" | "error"
    latency_ms: float
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0
    error_type: str | None = None
    estimated_cost_usd: float | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        """Return a JSON-serialisable view for logging or transport."""
        return asdict(self)


@runtime_checkable
class TelemetrySink(Protocol):
    """Receives a record after every engine call."""

    def record(self, call: CallRecord) -> None: ...


@runtime_checkable
class CallGate(Protocol):
    """Consulted before every engine call; raises to refuse it."""

    def check(self, *, model: str, provider: str, purpose: str) -> None: ...


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

_pricing_lock = threading.Lock()
_pricing: dict[str, ModelPricing] = {}


def set_model_pricing(model_id: str, pricing: ModelPricing) -> None:
    """Register USD-per-million-token pricing for *model_id*."""
    with _pricing_lock:
        _pricing[model_id] = pricing


def get_model_pricing(model_id: str) -> ModelPricing | None:
    """Return pricing for *model_id*, or ``None`` if none was supplied."""
    with _pricing_lock:
        return _pricing.get(model_id)


def load_pricing_from_env(raw: str | None = None) -> None:
    """Populate the pricing table from ``SPECORA_MODEL_PRICING``.

    Expects a JSON object mapping model ID to ``{"input": <usd per mtok>,
    "output": <usd per mtok>}``. Malformed input is logged and ignored --
    a typo in an optional cost-reporting variable must not stop the engine
    from answering.
    """
    payload = raw if raw is not None else os.environ.get("SPECORA_MODEL_PRICING", "")
    if not payload.strip():
        return
    try:
        parsed = json.loads(payload)
        for model_id, entry in parsed.items():
            set_model_pricing(
                model_id,
                ModelPricing(
                    input_per_mtok=float(entry["input"]),
                    output_per_mtok=float(entry["output"]),
                ),
            )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning(
            "Ignoring malformed SPECORA_MODEL_PRICING (%s: %s)",
            type(exc).__name__,
            exc,
        )


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Return the USD estimate for a call, or ``None`` if unpriced."""
    pricing = get_model_pricing(model_id)
    if pricing is None:
        return None
    return (
        input_tokens * pricing.input_per_mtok + output_tokens * pricing.output_per_mtok
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Sink / gate registries
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
_sinks: list[TelemetrySink] = []
_gates: list[CallGate] = []


def register_sink(sink: TelemetrySink) -> None:
    """Add *sink* to the set notified after each call."""
    with _registry_lock:
        if sink not in _sinks:
            _sinks.append(sink)


def unregister_sink(sink: TelemetrySink) -> None:
    """Remove *sink*; a no-op if it was never registered."""
    with _registry_lock:
        if sink in _sinks:
            _sinks.remove(sink)


def register_gate(gate: CallGate) -> None:
    """Add *gate* to the set consulted before each call."""
    with _registry_lock:
        if gate not in _gates:
            _gates.append(gate)


def unregister_gate(gate: CallGate) -> None:
    """Remove *gate*; a no-op if it was never registered."""
    with _registry_lock:
        if gate in _gates:
            _gates.remove(gate)


def check_gates(*, model: str, provider: str, purpose: str) -> None:
    """Consult every gate. Raises :class:`CallBlockedError` if any refuses."""
    with _registry_lock:
        gates = list(_gates)
    for gate in gates:
        gate.check(model=model, provider=provider, purpose=purpose)


def emit(record: CallRecord) -> None:
    """Deliver *record* to every sink and the structured log.

    Never raises: instrumentation failures must not become call failures.
    """
    logger.info("llm_call %s", json.dumps(record.as_dict(), sort_keys=True))
    with _registry_lock:
        sinks = list(_sinks)
    for sink in sinks:
        try:
            sink.record(record)
        except Exception:
            logger.exception("Telemetry sink %r failed", sink)


# ---------------------------------------------------------------------------
# Built-in aggregator
# ---------------------------------------------------------------------------


class UsageAggregator:
    """Thread-safe running totals over :class:`CallRecord` values."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[CallRecord] = []

    def record(self, call: CallRecord) -> None:
        """Sink protocol: accumulate *call*."""
        with self._lock:
            self._records.append(call)

    def reset(self) -> None:
        """Drop all accumulated records."""
        with self._lock:
            self._records.clear()

    def records(self) -> list[CallRecord]:
        """Return a snapshot of every record seen so far."""
        with self._lock:
            return list(self._records)

    def totals(self) -> dict:
        """Return aggregate usage.

        ``estimated_cost_usd`` is ``None`` when no call could be priced;
        ``unpriced_calls`` always states how many were excluded from it.
        """
        with self._lock:
            records = list(self._records)

        by_model: dict[str, dict] = {}
        input_tokens = output_tokens = 0
        cost = 0.0
        priced = unpriced = errors = 0

        for rec in records:
            input_tokens += rec.input_tokens
            output_tokens += rec.output_tokens
            if rec.estimated_cost_usd is None:
                unpriced += 1
            else:
                priced += 1
                cost += rec.estimated_cost_usd
            if rec.outcome != "ok":
                errors += 1

            bucket = by_model.setdefault(
                rec.model,
                {"calls": 0, "input_tokens": 0, "output_tokens": 0, "errors": 0},
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += rec.input_tokens
            bucket["output_tokens"] += rec.output_tokens
            if rec.outcome != "ok":
                bucket["errors"] += 1

        latencies = [r.latency_ms for r in records]
        return {
            "calls": len(records),
            "errors": errors,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": cost if priced else None,
            "priced_calls": priced,
            "unpriced_calls": unpriced,
            "mean_latency_ms": (sum(latencies) / len(latencies)) if latencies else 0.0,
            "max_latency_ms": max(latencies) if latencies else 0.0,
            "by_model": by_model,
        }


_default_aggregator = UsageAggregator()
register_sink(_default_aggregator)


def get_default_aggregator() -> UsageAggregator:
    """Return the process-wide aggregator the engine always feeds."""
    return _default_aggregator


load_pricing_from_env()

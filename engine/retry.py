"""Timeout and bounded-retry policy for provider transport calls.

Retries are safe here only because the thing being retried is a pure
request/response with no local side effects: the engine returns a response and
some *later* stage (the healer's applier, the factory's writer) decides what to
do with it. Nothing in this module, and nothing it wraps, mutates a contract,
a queue, or a file. Keep it that way -- the moment a side effect moves inside
the retried callable, retrying starts double-applying it.

Only transient transport failures are retried. A 4xx other than 408/409/425/429
means the request itself is wrong; resending it wastes the budget and, for 401
or 403, can trip lockouts. Those propagate on the first attempt.
"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from engine.telemetry import CallBlockedError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Status codes that indicate "try again", not "your request is wrong".
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Provider SDKs raise distinct classes for transport trouble but do not share a
# base, so match on name rather than importing every SDK to check isinstance.
RETRYABLE_EXCEPTION_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "APIConnectionTimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "InternalServerError",
        "ReadTimeout",
        "RemoteProtocolError",
        "ServiceUnavailableError",
        "Timeout",
        "TimeoutException",
    }
)


class RetryExhaustedError(Exception):
    """Raised when every attempt failed with a transient error.

    Chains the final underlying exception so the healer sees the real cause
    instead of a generic failure.
    """

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(
            f"LLM call failed after {attempts} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        )
        self.attempts = attempts
        self.last_error = last_error


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter."""

    timeout_seconds: float = 60.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    multiplier: float = 2.0
    jitter_ratio: float = 0.2

    @classmethod
    def from_env(cls) -> RetryPolicy:
        """Build a policy from ``SPECORA_LLM_*`` environment variables.

        Unparsable values fall back to the default rather than raising: a bad
        tuning knob must not make the engine unusable.
        """

        def _num(key: str, default: float, *, minimum: float) -> float:
            raw = os.environ.get(key, "").strip()
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError:
                logger.warning("Ignoring non-numeric %s=%r", key, raw)
                return default
            if value < minimum:
                logger.warning("Ignoring out-of-range %s=%r", key, raw)
                return default
            return value

        return cls(
            timeout_seconds=_num("SPECORA_LLM_TIMEOUT", 60.0, minimum=1.0),
            max_attempts=int(_num("SPECORA_LLM_MAX_ATTEMPTS", 3, minimum=1)),
            initial_backoff_seconds=_num(
                "SPECORA_LLM_BACKOFF", 0.5, minimum=0.0
            ),
            max_backoff_seconds=_num(
                "SPECORA_LLM_MAX_BACKOFF", 8.0, minimum=0.0
            ),
        )

    def backoff_for(self, attempt: int) -> float:
        """Return the sleep in seconds before *attempt* (1-based) is retried."""
        raw = self.initial_backoff_seconds * (self.multiplier ** (attempt - 1))
        capped = min(raw, self.max_backoff_seconds)
        if self.jitter_ratio <= 0:
            return capped
        # Jitter spreads retries so concurrent healer tickets do not all wake
        # up and hammer a rate-limited provider on the same tick.
        return capped * (1 + random.uniform(-self.jitter_ratio, self.jitter_ratio))


def is_transient(exc: BaseException) -> bool:
    """Return ``True`` if *exc* is worth retrying.

    A refusal from a spend gate is never transient. Neither is a 4xx other
    than the throttling/conflict codes.
    """
    if isinstance(exc, CallBlockedError):
        return False

    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    return type(exc).__name__ in RETRYABLE_EXCEPTION_NAMES


def call_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    label: str = "llm",
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    """Invoke *fn* under *policy*, returning ``(result, attempts_used)``.

    *fn* must be side-effect free -- see the module docstring.
    """
    last_error: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn(), attempt
        except Exception as exc:
            if not is_transient(exc):
                raise
            last_error = exc
            if attempt >= policy.max_attempts:
                break
            delay = policy.backoff_for(attempt)
            logger.warning(
                "%s attempt %d/%d failed (%s: %s); retrying in %.2fs",
                label,
                attempt,
                policy.max_attempts,
                type(exc).__name__,
                exc,
                delay,
            )
            sleep(delay)

    assert last_error is not None
    raise RetryExhaustedError(policy.max_attempts, last_error) from last_error

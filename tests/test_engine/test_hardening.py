"""Tests for structured output, retry policy, prompts, and telemetry.

None of these touch a provider SDK, so they run without the ``[llm]`` extra.
"""
from __future__ import annotations

import pytest

from engine.prompts import PromptNotFoundError, PromptRegistry, get_prompt
from engine.prompts.registry import Prompt
from engine.retry import (
    RetryExhaustedError,
    RetryPolicy,
    call_with_retry,
    is_transient,
)
from engine.structured import (
    StructuredOutputError,
    extract_json_object,
    schema_instruction,
)
from engine.telemetry import (
    CallBlockedError,
    CallRecord,
    ModelPricing,
    UsageAggregator,
    estimate_cost,
    load_pricing_from_env,
    set_model_pricing,
)


class _HttpError(Exception):
    """Stands in for a provider SDK's status-carrying exception."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class TestExtractJsonObject:
    """The old ``\\{[^}]+\\}`` regex failed on all of these."""

    def test_nested_object_in_fenced_block(self) -> None:
        text = 'Sure!\n```json\n{"command": "forge validate x", "meta": {"n": 1}}\n```'
        assert extract_json_object(text) == {
            "command": "forge validate x",
            "meta": {"n": 1},
        }

    def test_skips_a_brace_group_in_prose(self) -> None:
        text = 'Use {curly} braces. {"command": "forge graph", "explanation": "g"}'
        assert extract_json_object(text)["command"] == "forge graph"

    def test_braces_inside_a_string_literal(self) -> None:
        text = '{"command": "forge validate", "explanation": "matches {a} and {b}"}'
        assert extract_json_object(text)["explanation"] == "matches {a} and {b}"

    def test_bare_object(self) -> None:
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_object_after_prose(self) -> None:
        assert extract_json_object('I think:\n{"a": null}\nDone.') == {"a": None}

    @pytest.mark.parametrize(
        "text", ["", "   ", "I'm sorry, I cannot help.", "[1, 2, 3]", "{not json}"]
    )
    def test_failure_raises_rather_than_returning_none(self, text: str) -> None:
        with pytest.raises(StructuredOutputError):
            extract_json_object(text)

    def test_error_carries_the_raw_response(self) -> None:
        with pytest.raises(StructuredOutputError) as exc:
            extract_json_object("I refuse.")
        assert exc.value.raw == "I refuse."
        assert "I refuse." in str(exc.value)


class TestSchemaInstruction:
    def test_mentions_json_and_inlines_the_schema(self) -> None:
        out = schema_instruction("Be terse.", {"type": "object"})
        assert "Be terse." in out
        # OpenAI-compatible JSON mode rejects requests without the word.
        assert "JSON" in out
        assert '"type": "object"' in out


class TestRetryClassification:
    @pytest.mark.parametrize("code", [408, 409, 425, 429, 500, 502, 503, 504])
    def test_transient_statuses_retry(self, code: int) -> None:
        assert is_transient(_HttpError(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 422])
    def test_client_errors_do_not_retry(self, code: int) -> None:
        assert is_transient(_HttpError(code)) is False

    def test_timeouts_and_connection_errors_retry(self) -> None:
        assert is_transient(TimeoutError("slow")) is True
        assert is_transient(ConnectionError("refused")) is True

    def test_sdk_exception_names_retry(self) -> None:
        exc = type("APIConnectionError", (Exception,), {})()
        assert is_transient(exc) is True

    def test_budget_refusal_never_retries(self) -> None:
        assert is_transient(CallBlockedError("over budget")) is False

    def test_unknown_exception_does_not_retry(self) -> None:
        assert is_transient(ValueError("bad input")) is False


class TestCallWithRetry:
    @staticmethod
    def _policy(**kw) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=kw.get("max_attempts", 3),
            initial_backoff_seconds=0.0,
            max_backoff_seconds=0.0,
            jitter_ratio=0.0,
        )

    def test_succeeds_without_retrying(self) -> None:
        calls = []

        def ok():
            calls.append(1)
            return "done"

        result, attempts = call_with_retry(ok, self._policy())
        assert result == "done"
        assert attempts == 1
        assert len(calls) == 1

    def test_retries_transient_then_succeeds(self) -> None:
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise _HttpError(503)
            return "done"

        result, attempts = call_with_retry(flaky, self._policy())
        assert result == "done"
        assert attempts == 3

    def test_non_transient_propagates_on_first_attempt(self) -> None:
        calls = []

        def unauthorised():
            calls.append(1)
            raise _HttpError(401)

        with pytest.raises(_HttpError):
            call_with_retry(unauthorised, self._policy(max_attempts=5))
        assert len(calls) == 1

    def test_exhaustion_chains_the_real_cause(self) -> None:
        def always_429():
            raise _HttpError(429)

        with pytest.raises(RetryExhaustedError) as exc:
            call_with_retry(always_429, self._policy(max_attempts=2))
        assert exc.value.attempts == 2
        assert isinstance(exc.value.last_error, _HttpError)

    def test_backoff_is_bounded(self) -> None:
        policy = RetryPolicy(
            initial_backoff_seconds=1.0,
            max_backoff_seconds=4.0,
            multiplier=2.0,
            jitter_ratio=0.0,
        )
        assert [policy.backoff_for(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 4.0]

    def test_bad_env_values_fall_back_to_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("SPECORA_LLM_TIMEOUT", "not-a-number")
        monkeypatch.setenv("SPECORA_LLM_MAX_ATTEMPTS", "-4")
        policy = RetryPolicy.from_env()
        assert policy.timeout_seconds == RetryPolicy().timeout_seconds
        assert policy.max_attempts == RetryPolicy().max_attempts


class TestTelemetry:
    def test_aggregates_tokens_and_errors(self) -> None:
        agg = UsageAggregator()
        agg.record(
            CallRecord("m1", "p", "heal", "ok", 10.0, 1, input_tokens=100, output_tokens=20)
        )
        agg.record(
            CallRecord("m1", "p", "heal", "error", 30.0, 3, error_type="APITimeoutError")
        )

        totals = agg.totals()
        assert totals["calls"] == 2
        assert totals["errors"] == 1
        assert totals["input_tokens"] == 100
        assert totals["total_tokens"] == 120
        assert totals["by_model"]["m1"]["errors"] == 1

    def test_unpriced_calls_report_none_rather_than_zero(self) -> None:
        agg = UsageAggregator()
        agg.record(CallRecord("unpriced", "p", "x", "ok", 1.0, 1, input_tokens=1000))
        totals = agg.totals()
        assert totals["estimated_cost_usd"] is None
        assert totals["unpriced_calls"] == 1

    def test_cost_is_computed_once_pricing_is_supplied(self) -> None:
        set_model_pricing("priced-model", ModelPricing(2.0, 10.0))
        assert estimate_cost("priced-model", 1_000_000, 1_000_000) == pytest.approx(12.0)

    def test_malformed_pricing_env_is_ignored(self) -> None:
        load_pricing_from_env("{not json")
        assert estimate_cost("still-unknown", 100, 100) is None

    def test_pricing_from_env(self) -> None:
        load_pricing_from_env('{"env-model": {"input": 1.0, "output": 2.0}}')
        assert estimate_cost("env-model", 1_000_000, 0) == pytest.approx(1.0)


class TestPromptRegistry:
    def test_cli_router_prompt_is_registered(self) -> None:
        prompt = get_prompt("cli_router")
        assert prompt.version >= 1
        assert prompt.checksum
        assert prompt.ref.startswith("cli_router@v1:")

    def test_render_substitutes_without_breaking_json_braces(self) -> None:
        body = 'List:\n$commands\nReply {"command": null}'
        prompt = Prompt("t", 1, "d", body)
        rendered = prompt.render(commands="- a\n- b")
        assert "- a" in rendered
        assert '{"command": null}' in rendered

    def test_reregistering_a_version_with_a_new_body_is_refused(self) -> None:
        reg = PromptRegistry()
        reg.register(Prompt("p", 1, "d", "original"))
        with pytest.raises(ValueError, match="new version"):
            reg.register(Prompt("p", 1, "d", "edited"))

    def test_history_keeps_old_versions(self) -> None:
        reg = PromptRegistry()
        reg.register(Prompt("p", 1, "d", "v1 body"))
        reg.register(Prompt("p", 2, "d", "v2 body"))
        assert [p.body for p in reg.history("p")] == ["v1 body", "v2 body"]
        assert reg.get("p").version == 2
        assert reg.get("p", 1).body == "v1 body"

    def test_unknown_prompt_raises(self) -> None:
        with pytest.raises(PromptNotFoundError):
            PromptRegistry().get("nope")

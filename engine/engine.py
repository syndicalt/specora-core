"""LLM Engine — the unified entry point for all LLM interactions.

``LLMEngine`` wraps provider-specific SDKs behind a single interface.
Create one from an ``EngineConfig`` (or use ``from_env()`` for auto-detection),
then call ``ask()`` for simple Q&A, ``ask_json()`` when the answer must be a
structured object, or ``chat()`` for full conversations with tool use.

Every call is bounded by a timeout, retried under
:class:`engine.retry.RetryPolicy` on transient transport failures only, gated
by any registered spend guard, and recorded to :mod:`engine.telemetry`.

Example::

    engine = LLMEngine.from_env()
    answer = engine.ask("Summarise this incident.", system="You are an ITSM expert.")
"""
from __future__ import annotations

import logging
import time
from typing import Any

from engine import telemetry
from engine.config import EngineConfig, EngineConfigError
from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition
from engine.retry import call_with_retry
from engine.structured import StructuredOutputError, extract_json_object

logger = logging.getLogger(__name__)


def _normalise_usage(usage: Any) -> tuple[int, int]:
    """Return ``(input_tokens, output_tokens)`` from any provider's usage dict.

    Anthropic reports ``input_tokens``/``output_tokens``; the OpenAI wire
    format reports ``prompt_tokens``/``completion_tokens``. Aggregation needs
    one vocabulary.
    """
    if not isinstance(usage, dict):
        return 0, 0

    def _pick(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int):
                return value
        return 0

    return (
        _pick("input_tokens", "prompt_tokens"),
        _pick("output_tokens", "completion_tokens"),
    )


class LLMEngine:
    """Unified LLM interface that delegates to a concrete provider.

    The engine is configured once and then used for multiple requests.
    It handles provider instantiation, message formatting, and response
    normalisation transparently.
    """

    def __init__(self, config: EngineConfig) -> None:
        """Initialise the engine with a resolved configuration.

        Args:
            config: Fully resolved ``EngineConfig`` containing model ID,
                    capabilities, API key, retry policy, and strategy.

        Raises:
            EngineConfigError: If the provider specified in *config* is
                               not supported.
        """
        self.config = config
        self.model_id = config.model_id
        self.strategy = config.strategy
        self._provider: Provider = self._create_provider(config)
        logger.info(
            "LLMEngine initialised: model=%s, provider=%s, strategy=%s, timeout=%.0fs",
            config.model_id,
            config.capabilities.provider,
            config.strategy,
            config.retry_policy.timeout_seconds,
        )

    @classmethod
    def from_env(cls) -> LLMEngine:
        """Create an engine by probing environment variables.

        Delegates to ``EngineConfig.from_env()`` to resolve the provider,
        model, and API key from the environment.

        Raises:
            EngineConfigError: If no usable provider is configured.
        """
        config = EngineConfig.from_env()
        return cls(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ask(self, question: str, system: str = "", *, purpose: str = "ask") -> str:
        """Simple question-and-answer interface.

        Wraps the question in a single user message, sends it to the
        provider, and returns the text content of the response.

        Args:
            question: The question to ask the model.
            system: Optional system prompt to set context.
            purpose: Label recorded with the call for usage attribution.

        Returns:
            The model's text response as a string.
        """
        messages = [Message(role="user", content=question)]
        response = self.chat(
            messages,
            system=system or None,
            purpose=purpose,
        )
        return response.content

    def ask_json(
        self,
        question: str,
        *,
        schema: dict[str, Any],
        system: str = "",
        purpose: str = "ask_json",
    ) -> dict:
        """Ask for a JSON object and return it parsed.

        Engages the provider's structured-output mode when the model declares
        support for it, and falls back to extracting a balanced object from
        the reply otherwise.

        Args:
            question: The request to send.
            schema: JSON Schema describing the expected object.
            system: Optional system prompt.
            purpose: Label recorded with the call for usage attribution.

        Returns:
            The parsed object.

        Raises:
            StructuredOutputError: If the reply contains no JSON object. The
                caller must handle this rather than collapsing it into a
                generic failure — "the model refused" and "the model produced
                the wrong shape" need different responses.
        """
        structured = self.config.capabilities.supports_structured_output
        response = self.chat(
            [Message(role="user", content=question)],
            system=system or None,
            response_schema=schema if structured else None,
            purpose=purpose,
        )
        try:
            return extract_json_object(response.content)
        except StructuredOutputError:
            logger.warning(
                "Structured output failed: model=%s structured_mode=%s purpose=%s",
                self.model_id,
                structured,
                purpose,
            )
            raise

    def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_schema: dict[str, Any] | None = None,
        purpose: str = "chat",
    ) -> LLMResponse:
        """Full conversation interface with tool support.

        Forwards the request to the underlying provider under the configured
        retry policy and returns a normalised ``LLMResponse``.

        Args:
            messages: Conversation history as ``Message`` objects.
            system: Optional system prompt.
            tools: Optional tool definitions the model may invoke.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.
            response_schema: JSON Schema forcing a structured reply.
            purpose: Label recorded with the call for usage attribution.

        Returns:
            Normalised ``LLMResponse`` containing content, tool calls, stop
            reason, and usage statistics.

        Raises:
            engine.telemetry.CallBlockedError: If a registered gate (e.g. a
                spend ceiling) refuses the call.
            engine.retry.RetryExhaustedError: If every attempt hit a transient
                failure.
        """
        logger.debug(
            "Engine chat: messages=%d, tools=%d, temp=%.2f, structured=%s",
            len(messages),
            len(tools) if tools else 0,
            temperature,
            response_schema is not None,
        )

        provider_name = self.config.capabilities.provider
        telemetry.check_gates(
            model=self.model_id, provider=provider_name, purpose=purpose
        )

        # Only the transport call goes inside the retry. It has no local side
        # effects, which is the property that makes resending it safe; anything
        # that writes must stay on the caller's side of this boundary.
        def _send() -> LLMResponse:
            return self._provider.chat(
                messages,
                system=system,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                response_schema=response_schema,
            )

        started = time.perf_counter()
        attempts = 1
        try:
            response, attempts = call_with_retry(
                _send, self.config.retry_policy, label=f"{provider_name}.chat"
            )
        except Exception as exc:
            self._record(
                purpose=purpose,
                provider=provider_name,
                started=started,
                attempts=self.config.retry_policy.max_attempts,
                outcome="error",
                error_type=type(exc).__name__,
            )
            raise

        input_tokens, output_tokens = _normalise_usage(
            getattr(response, "usage", None)
        )
        self._record(
            purpose=purpose,
            provider=provider_name,
            started=started,
            attempts=attempts,
            outcome="ok",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return response

    # ------------------------------------------------------------------
    # Instrumentation
    # ------------------------------------------------------------------

    def _record(
        self,
        *,
        purpose: str,
        provider: str,
        started: float,
        attempts: int,
        outcome: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error_type: str | None = None,
    ) -> None:
        """Emit one :class:`~engine.telemetry.CallRecord` for this call."""
        telemetry.emit(
            telemetry.CallRecord(
                model=self.model_id,
                provider=provider,
                purpose=purpose,
                outcome=outcome,
                latency_ms=(time.perf_counter() - started) * 1000,
                attempts=attempts,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_type=error_type,
                estimated_cost_usd=telemetry.estimate_cost(
                    self.model_id, input_tokens, output_tokens
                ),
            )
        )

    # ------------------------------------------------------------------
    # Provider factory
    # ------------------------------------------------------------------

    @staticmethod
    def _create_provider(config: EngineConfig) -> Provider:
        """Instantiate the correct provider based on config.

        Args:
            config: Engine configuration with provider info.

        Returns:
            A concrete ``Provider`` instance.

        Raises:
            EngineConfigError: If the provider is not recognised.
        """
        provider_name = config.capabilities.provider
        timeout = config.retry_policy.timeout_seconds

        if provider_name == "anthropic":
            from engine.providers.anthropic import AnthropicProvider

            if not config.api_key:
                raise EngineConfigError("Anthropic provider requires an API key.")
            return AnthropicProvider(
                api_key=config.api_key, model=config.model_id, timeout=timeout
            )

        if provider_name == "openai":
            from engine.providers.openai import OpenAIProvider

            if not config.api_key:
                raise EngineConfigError("OpenAI provider requires an API key.")
            return OpenAIProvider(
                api_key=config.api_key,
                model=config.model_id,
                base_url=config.base_url,
                timeout=timeout,
            )

        if provider_name == "zai":
            from engine.providers.zai import ZAIProvider

            if not config.api_key:
                raise EngineConfigError("Z.AI provider requires an API key (ZAI_API_KEY).")
            return ZAIProvider(
                api_key=config.api_key, model=config.model_id, timeout=timeout
            )

        if provider_name == "ollama":
            from engine.providers.ollama import OllamaProvider

            # No key check: a local Ollama server has no authentication.
            return OllamaProvider(
                model=config.model_id, base_url=config.base_url, timeout=timeout
            )

        raise EngineConfigError(
            f"Unsupported provider: {provider_name!r}. "
            f"Supported providers: anthropic, openai, zai, ollama."
        )

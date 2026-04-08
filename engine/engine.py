"""LLM Engine — the unified entry point for all LLM interactions.

``LLMEngine`` wraps provider-specific SDKs behind a single interface.
Create one from an ``EngineConfig`` (or use ``from_env()`` for auto-detection),
then call ``ask()`` for simple Q&A or ``chat()`` for full conversations
with tool use.

Example::

    engine = LLMEngine.from_env()
    answer = await engine.ask("Summarise this incident.", system="You are an ITSM expert.")
"""
from __future__ import annotations

import logging
from typing import Any

from engine.config import EngineConfig, EngineConfigError
from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition

logger = logging.getLogger(__name__)


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
                    capabilities, API key, and strategy.

        Raises:
            EngineConfigError: If the provider specified in *config* is
                               not supported.
        """
        self.config = config
        self.model_id = config.model_id
        self.strategy = config.strategy
        self._provider: Provider = self._create_provider(config)
        logger.info(
            "LLMEngine initialised: model=%s, provider=%s, strategy=%s",
            config.model_id,
            config.capabilities.provider,
            config.strategy,
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

    def ask(self, question: str, system: str = "") -> str:
        """Simple question-and-answer interface.

        Wraps the question in a single user message, sends it to the
        provider, and returns the text content of the response.

        Args:
            question: The question to ask the model.
            system: Optional system prompt to set context.

        Returns:
            The model's text response as a string.
        """
        messages = [Message(role="user", content=question)]
        response = self.chat(
            messages,
            system=system or None,
        )
        return response.content

    def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Full conversation interface with tool support.

        Forwards the request to the underlying provider and returns
        a normalised ``LLMResponse``.

        Args:
            messages: Conversation history as ``Message`` objects.
            system: Optional system prompt.
            tools: Optional tool definitions the model may invoke.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            Normalised ``LLMResponse`` containing content, tool calls,
            stop reason, and usage statistics.
        """
        logger.debug(
            "Engine chat: messages=%d, tools=%d, temp=%.2f",
            len(messages),
            len(tools) if tools else 0,
            temperature,
        )
        return self._provider.chat(
            messages,
            system=system,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
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

        if provider_name == "anthropic":
            from engine.providers.anthropic import AnthropicProvider

            if not config.api_key:
                raise EngineConfigError("Anthropic provider requires an API key.")
            return AnthropicProvider(api_key=config.api_key, model=config.model_id)

        if provider_name == "openai":
            from engine.providers.openai import OpenAIProvider

            if not config.api_key:
                raise EngineConfigError("OpenAI provider requires an API key.")
            return OpenAIProvider(
                api_key=config.api_key,
                model=config.model_id,
                base_url=config.base_url,
            )

        if provider_name == "zai":
            from engine.providers.zai import ZAIProvider

            if not config.api_key:
                raise EngineConfigError("Z.AI provider requires an API key (ZAI_API_KEY).")
            return ZAIProvider(api_key=config.api_key, model=config.model_id)

        raise EngineConfigError(
            f"Unsupported provider: {provider_name!r}. "
            f"Supported providers: anthropic, openai, zai."
        )

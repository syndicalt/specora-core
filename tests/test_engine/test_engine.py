"""Tests for the LLM engine — provider creation, ask(), and chat().

All tests mock the provider layer so no real API calls are made.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config import EngineConfig, EngineConfigError
from engine.engine import LLMEngine
from engine.providers.base import LLMResponse, Message, ToolDefinition
from engine.registry import ModelCapabilities


def _make_config(
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-4-6",
    api_key: str = "sk-test-key",
) -> EngineConfig:
    """Build a minimal EngineConfig for testing."""
    caps = ModelCapabilities(
        provider=provider,
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="frontier",
    )
    return EngineConfig(
        model_id=model_id,
        capabilities=caps,
        api_key=api_key,
        base_url=None,
        strategy=caps.best_strategy(),
    )


class TestEngineCreation:
    """Verify engine instantiation from config and environment."""

    def test_engine_creates_from_config(self) -> None:
        """Create EngineConfig with anthropic caps, create LLMEngine, verify model_id and strategy."""
        config = _make_config(provider="anthropic")
        engine = LLMEngine(config)

        assert engine.config.model_id == "claude-sonnet-4-6"
        assert engine.config.strategy == "tools"
        assert engine.config.capabilities.provider == "anthropic"

    def test_engine_creates_from_openai_config(self) -> None:
        """Verify engine works with OpenAI provider config."""
        config = _make_config(provider="openai", model_id="gpt-4o")
        engine = LLMEngine(config)

        assert engine.config.model_id == "gpt-4o"
        assert engine.config.capabilities.provider == "openai"

    def test_engine_from_env_raises_without_keys(self) -> None:
        """Patch env empty, verify EngineConfigError."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EngineConfigError):
                LLMEngine.from_env()


class TestEngineAsk:
    """Verify the ask() convenience method."""

    def test_engine_ask_returns_string(self) -> None:
        """Create engine, mock provider, verify ask() returns text."""
        config = _make_config()
        engine = LLMEngine(config)

        mock_response = LLMResponse(
            content="The answer is 42.",
            tool_calls=[],
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        engine._provider = MagicMock()
        engine._provider.chat = MagicMock(return_value=mock_response)

        result = engine.ask("What is the meaning of life?")

        assert result == "The answer is 42."
        engine._provider.chat.assert_called_once()

    def test_engine_ask_with_system_prompt(self) -> None:
        """Verify system prompt is passed to provider.chat()."""
        config = _make_config()
        engine = LLMEngine(config)

        mock_response = LLMResponse(
            content="I am a helpful assistant.",
            tool_calls=[],
            stop_reason="end_turn",
            usage={"input_tokens": 15, "output_tokens": 8},
        )
        engine._provider = MagicMock()
        engine._provider.chat = MagicMock(return_value=mock_response)

        result = engine.ask("Who are you?", system="You are a helpful assistant.")

        assert result == "I am a helpful assistant."
        call_kwargs = engine._provider.chat.call_args
        assert call_kwargs.kwargs["system"] == "You are a helpful assistant."


class TestEngineChat:
    """Verify the full chat() interface."""

    def test_engine_chat_returns_llm_response(self) -> None:
        """Verify chat() returns a full LLMResponse."""
        config = _make_config()
        engine = LLMEngine(config)

        mock_response = LLMResponse(
            content="Hello!",
            tool_calls=[],
            stop_reason="end_turn",
            usage={"input_tokens": 5, "output_tokens": 3},
        )
        engine._provider = AsyncMock()
        engine._provider.chat = AsyncMock(return_value=mock_response)

        messages = [Message(role="user", content="Hi")]
        result = asyncio.run(engine.chat(messages))

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello!"

    def test_engine_chat_passes_tools(self) -> None:
        """Verify tools are forwarded to the provider."""
        config = _make_config()
        engine = LLMEngine(config)

        mock_response = LLMResponse(
            content="",
            tool_calls=[{"id": "t1", "name": "get_weather", "input": {"city": "NYC"}}],
            stop_reason="tool_use",
            usage={"input_tokens": 20, "output_tokens": 15},
        )
        engine._provider = AsyncMock()
        engine._provider.chat = AsyncMock(return_value=mock_response)

        tools = [
            ToolDefinition(
                name="get_weather",
                description="Get the weather for a city.",
                parameters={"type": "object", "properties": {"city": {"type": "string"}}},
            )
        ]
        messages = [Message(role="user", content="What's the weather in NYC?")]
        result = asyncio.run(engine.chat(messages, tools=tools))

        assert result.stop_reason == "tool_use"
        assert len(result.tool_calls) == 1
        call_kwargs = engine._provider.chat.call_args
        assert call_kwargs.kwargs["tools"] == tools


class TestProviderFactory:
    """Verify _create_provider returns the correct provider type."""

    def test_creates_anthropic_provider(self) -> None:
        config = _make_config(provider="anthropic")
        engine = LLMEngine(config)
        assert engine._provider.provider_name() == "anthropic"

    def test_creates_openai_provider(self) -> None:
        config = _make_config(provider="openai", model_id="gpt-4o")
        engine = LLMEngine(config)
        assert engine._provider.provider_name() == "openai"

    def test_unknown_provider_raises(self) -> None:
        caps = ModelCapabilities(
            provider="unknown_provider",
            supports_tools=False,
            supports_structured_output=False,
            max_context=1000,
            tier="local",
        )
        config = EngineConfig(
            model_id="test",
            capabilities=caps,
            api_key=None,
            base_url=None,
            strategy="prompt",
        )
        with pytest.raises(EngineConfigError, match="Unsupported provider"):
            LLMEngine(config)

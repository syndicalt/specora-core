"""Tests for the model registry and capabilities system."""
from __future__ import annotations

from engine.registry import ModelCapabilities, ModelRegistry


class TestModelRegistry:
    """Verify the model registry discovers and filters models correctly."""

    def setup_method(self) -> None:
        self.reg = ModelRegistry()

    def test_registry_has_claude_models(self) -> None:
        caps = self.reg.get("claude-sonnet-4-6")
        assert caps is not None
        assert caps.provider == "anthropic"
        assert caps.supports_tools is True
        assert caps.supports_structured_output is True
        assert caps.max_context >= 200_000

    def test_registry_returns_none_for_unknown(self) -> None:
        assert self.reg.get("nonexistent") is None

    def test_registry_lists_all_models(self) -> None:
        models = self.reg.list_models()
        assert len(models) > 0
        assert "claude-sonnet-4-6" in models

    def test_registry_lists_by_provider(self) -> None:
        models = self.reg.list_models(provider="anthropic")
        assert len(models) > 0
        for model_id in models:
            caps = self.reg.get(model_id)
            assert caps is not None
            assert caps.provider == "anthropic"


class TestModelCapabilities:
    """Verify the best_strategy selection logic."""

    def test_capabilities_best_strategy_with_tools(self) -> None:
        caps = ModelCapabilities(
            provider="anthropic",
            supports_tools=True,
            supports_structured_output=True,
            max_context=200_000,
            tier="frontier",
        )
        assert caps.best_strategy() == "tools"

    def test_capabilities_best_strategy_structured_fallback(self) -> None:
        caps = ModelCapabilities(
            provider="openai",
            supports_tools=False,
            supports_structured_output=True,
            max_context=128_000,
            tier="mid",
        )
        assert caps.best_strategy() == "structured_output"

    def test_capabilities_best_strategy_prompt_fallback(self) -> None:
        caps = ModelCapabilities(
            provider="local",
            supports_tools=False,
            supports_structured_output=False,
            max_context=32_000,
            tier="local",
        )
        assert caps.best_strategy() == "prompt"

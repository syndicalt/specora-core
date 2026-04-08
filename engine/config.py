"""Engine configuration — resolve model, API key, and strategy from environment.

``EngineConfig.from_env()`` probes environment variables in priority order
so the caller never has to wire provider details manually.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from engine.registry import ModelCapabilities, ModelRegistry


class EngineConfigError(Exception):
    """Raised when the engine cannot be configured (missing key, unknown model, etc.)."""


@dataclass(frozen=True)
class EngineConfig:
    """Resolved configuration for an LLM engine session."""

    model_id: str
    capabilities: ModelCapabilities
    api_key: str | None
    base_url: str | None
    strategy: str

    @classmethod
    def from_env(cls) -> EngineConfig:
        """Build config by probing environment variables.

        Resolution order:
        1. ``SPECORA_AI_MODEL`` — explicit model override.
        2. ``ANTHROPIC_API_KEY`` — selects claude-sonnet-4-6.
        3. ``OPENAI_API_KEY`` — selects gpt-4o.
        4. ``XAI_API_KEY`` — selects gpt-4o with xAI base URL.
        5. ``OLLAMA_BASE_URL`` — selects llama3.3:70b (local).

        Raises ``EngineConfigError`` if no usable provider is found.
        """
        def _env(key: str) -> str:
            """Get env var, strip whitespace and quotes, return empty string if unset."""
            return os.environ.get(key, "").strip().strip("'\"")

        registry = ModelRegistry()

        model_id = _env("SPECORA_AI_MODEL")
        api_key: str | None = None
        base_url: str | None = None

        if model_id:
            caps = registry.get(model_id)
            if caps is None:
                raise EngineConfigError(f"Unknown model: {model_id}")
            if caps.provider == "anthropic":
                api_key = _env("ANTHROPIC_API_KEY")
            elif caps.provider == "zai":
                api_key = _env("ZAI_API_KEY")
            elif caps.provider == "openai":
                if model_id.startswith("grok"):
                    api_key = _env("XAI_API_KEY")
                    base_url = "https://api.x.ai/v1"
                else:
                    api_key = _env("OPENAI_API_KEY") or _env("XAI_API_KEY")
                    if _env("XAI_API_KEY") and not _env("OPENAI_API_KEY"):
                        base_url = "https://api.x.ai/v1"
            return cls(
                model_id=model_id,
                capabilities=caps,
                api_key=api_key,
                base_url=base_url,
                strategy=caps.best_strategy(),
            )

        # Auto-detect provider from available keys
        anthropic_key = _env("ANTHROPIC_API_KEY")
        if anthropic_key:
            model_id = "claude-sonnet-4-6"
            caps = registry.get(model_id)
            assert caps is not None
            return cls(
                model_id=model_id,
                capabilities=caps,
                api_key=anthropic_key,
                base_url=None,
                strategy=caps.best_strategy(),
            )

        openai_key = _env("OPENAI_API_KEY")
        if openai_key:
            model_id = "gpt-4o"
            caps = registry.get(model_id)
            assert caps is not None
            return cls(
                model_id=model_id,
                capabilities=caps,
                api_key=openai_key,
                base_url=None,
                strategy=caps.best_strategy(),
            )

        xai_key = _env("XAI_API_KEY")
        if xai_key:
            model_id = "grok-3-mini"
            caps = registry.get(model_id)
            assert caps is not None
            return cls(
                model_id=model_id,
                capabilities=caps,
                api_key=xai_key,
                base_url="https://api.x.ai/v1",
                strategy=caps.best_strategy(),
            )

        zai_key = _env("ZAI_API_KEY")
        if zai_key:
            model_id = "glm-4.7-flash"
            caps = registry.get(model_id)
            assert caps is not None
            return cls(
                model_id=model_id,
                capabilities=caps,
                api_key=zai_key,
                base_url=None,  # ZAIProvider handles its own URL
                strategy=caps.best_strategy(),
            )

        ollama_url = _env("OLLAMA_BASE_URL")
        if ollama_url:
            model_id = "llama3.3:70b"
            caps = registry.get(model_id)
            assert caps is not None
            return cls(
                model_id=model_id,
                capabilities=caps,
                api_key=None,
                base_url=ollama_url,
                strategy=caps.best_strategy(),
            )

        raise EngineConfigError(
            "No LLM provider configured. Set one of: SPECORA_AI_MODEL, "
            "ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, ZAI_API_KEY, or OLLAMA_BASE_URL."
        )

"""Model registry — catalog of known LLM models and their capabilities.

The registry maps model IDs to capability metadata so the engine can select
the best interaction strategy (tool use, structured output, or raw prompt)
without the caller needing to know provider-specific details.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCapabilities:
    """Declares what a model can do so the engine can pick a strategy."""

    provider: str
    supports_tools: bool
    supports_structured_output: bool
    max_context: int
    tier: str  # "frontier", "mid", "local"
    notes: str = ""

    def best_strategy(self) -> str:
        """Return the strongest interaction mode this model supports.

        Priority: tools > structured_output > prompt.
        """
        if self.supports_tools:
            return "tools"
        if self.supports_structured_output:
            return "structured_output"
        return "prompt"


_BUILTIN_MODELS: dict[str, ModelCapabilities] = {
    # --- Anthropic ---
    "claude-opus-4-6": ModelCapabilities(
        provider="anthropic",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="frontier",
        notes="Most capable Anthropic model.",
    ),
    "claude-sonnet-4-6": ModelCapabilities(
        provider="anthropic",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="frontier",
        notes="Best balance of speed and capability.",
    ),
    "claude-haiku-4-5": ModelCapabilities(
        provider="anthropic",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="mid",
        notes="Fast and cheap.",
    ),
    # --- OpenAI ---
    "gpt-4o": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="frontier",
    ),
    "gpt-4o-mini": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="mid",
    ),
    "o3-mini": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="frontier",
        notes="Reasoning model.",
    ),
    # --- xAI (Grok) ---
    "grok-3": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=131_072,
        tier="frontier",
        notes="xAI Grok 3. Uses OpenAI-compatible API at https://api.x.ai/v1.",
    ),
    "grok-3-mini": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=131_072,
        tier="frontier",
        notes="xAI Grok 3 Mini. Fast and cost-effective.",
    ),
    # --- Google (via OpenAI-compatible endpoint) ---
    "gemini-2.5-pro": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=1_000_000,
        tier="frontier",
        notes="Uses OpenAI-compatible API.",
    ),
    # --- Z.AI (GLM) — requires JWT-signed auth ---
    "glm-5.1": ModelCapabilities(
        provider="zai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="frontier",
        notes="Z.AI flagship. JWT auth via api.z.ai/api/paas/v4/",
    ),
    "glm-5": ModelCapabilities(
        provider="zai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="frontier",
        notes="Z.AI standard flagship.",
    ),
    "glm-4.7-flash": ModelCapabilities(
        provider="zai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="mid",
        notes="Z.AI free tier. Fast.",
    ),
    "glm-4.5-flash": ModelCapabilities(
        provider="zai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="mid",
        notes="Z.AI free tier.",
    ),
    # --- Local (Ollama) ---
    "llama3.3:70b": ModelCapabilities(
        provider="local",
        supports_tools=False,
        supports_structured_output=True,
        max_context=128_000,
        tier="local",
    ),
    "qwen2.5:32b": ModelCapabilities(
        provider="local",
        supports_tools=False,
        supports_structured_output=True,
        max_context=32_000,
        tier="local",
    ),
    "mistral:7b": ModelCapabilities(
        provider="local",
        supports_tools=False,
        supports_structured_output=False,
        max_context=32_000,
        tier="local",
    ),
}


class ModelRegistry:
    """Lookup table for model capabilities.

    Starts with built-in models and can be extended at runtime.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelCapabilities] = dict(_BUILTIN_MODELS)

    def get(self, model_id: str) -> ModelCapabilities | None:
        """Return capabilities for *model_id*, or ``None`` if unknown."""
        return self._models.get(model_id)

    def list_models(self, *, provider: str | None = None) -> list[str]:
        """Return model IDs, optionally filtered by provider."""
        if provider is None:
            return list(self._models.keys())
        return [
            mid for mid, caps in self._models.items() if caps.provider == provider
        ]

    def recommended(self) -> str:
        """Return the default recommended model ID."""
        return "claude-sonnet-4-6"

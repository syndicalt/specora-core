"""Engine configuration — resolve model, API key, and strategy from environment.

``EngineConfig.from_env()`` probes environment variables in priority order
so the caller never has to wire provider details manually.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from engine.registry import UNKNOWN_OLLAMA_MODEL, ModelCapabilities, ModelRegistry
from engine.retry import RetryPolicy

# Google exposes Gemini through an OpenAI-compatible surface, so it needs no
# provider of its own — only the right base URL.
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
XAI_BASE_URL = "https://api.x.ai/v1"

DEFAULT_GOOGLE_MODEL = "gemini-2.5-pro"
DEFAULT_OLLAMA_MODEL = "llama3.3:70b"

# A 70B model generating on local hardware routinely exceeds a minute, so the
# hosted-provider default would time out every call.
OLLAMA_DEFAULT_TIMEOUT_SECONDS = 300.0


class EngineConfigError(Exception):
    """Raised when the engine cannot be configured (missing key, unknown model, etc.)."""


def _env(key: str) -> str:
    """Get env var, strip whitespace and quotes, return empty string if unset."""
    return os.environ.get(key, "").strip().strip("'\"")


@dataclass(frozen=True)
class EngineConfig:
    """Resolved configuration for an LLM engine session."""

    model_id: str
    capabilities: ModelCapabilities
    api_key: str | None
    base_url: str | None
    strategy: str
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_env(cls) -> EngineConfig:
        """Build config by probing environment variables.

        Resolution order:
        1. ``SPECORA_AI_MODEL`` — explicit model override.
        2. ``ANTHROPIC_API_KEY`` — selects claude-sonnet-4-6.
        3. ``OPENAI_API_KEY`` — selects gpt-4o.
        4. ``XAI_API_KEY`` — selects grok-3-mini.
        5. ``ZAI_API_KEY`` — selects glm-4.7-flash.
        6. ``GOOGLE_API_KEY`` — selects gemini-2.5-pro.
        7. ``OLLAMA_BASE_URL`` — selects ``OLLAMA_MODEL`` or llama3.3:70b.

        Timeout and retry behaviour come from ``SPECORA_LLM_*`` (see
        :class:`engine.retry.RetryPolicy`).

        Raises ``EngineConfigError`` if no usable provider is found.
        """
        registry = ModelRegistry()
        policy = RetryPolicy.from_env()

        explicit_model = _env("SPECORA_AI_MODEL")
        if explicit_model:
            return cls._from_explicit_model(explicit_model, registry, policy)

        anthropic_key = _env("ANTHROPIC_API_KEY")
        if anthropic_key:
            return cls._build("claude-sonnet-4-6", registry, policy, api_key=anthropic_key)

        openai_key = _env("OPENAI_API_KEY")
        if openai_key:
            return cls._build("gpt-4o", registry, policy, api_key=openai_key)

        xai_key = _env("XAI_API_KEY")
        if xai_key:
            return cls._build(
                "grok-3-mini", registry, policy, api_key=xai_key, base_url=XAI_BASE_URL
            )

        zai_key = _env("ZAI_API_KEY")
        if zai_key:
            # ZAIProvider signs its own JWT against a fixed URL.
            return cls._build("glm-4.7-flash", registry, policy, api_key=zai_key)

        google_key = _env("GOOGLE_API_KEY")
        if google_key:
            return cls._build(
                DEFAULT_GOOGLE_MODEL,
                registry,
                policy,
                api_key=google_key,
                base_url=GOOGLE_BASE_URL,
            )

        ollama_url = _env("OLLAMA_BASE_URL")
        if ollama_url:
            model_id = _env("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
            caps = cls._ollama_capabilities(model_id, registry)
            return cls._build(
                model_id,
                registry,
                cls._ollama_policy(policy),
                base_url=ollama_url,
                capabilities=caps,
            )

        raise EngineConfigError(
            "No LLM provider configured. Set one of: SPECORA_AI_MODEL, "
            "ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, ZAI_API_KEY, "
            "GOOGLE_API_KEY, or OLLAMA_BASE_URL."
        )

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ollama_policy(policy: RetryPolicy) -> RetryPolicy:
        """Widen the timeout for local generation unless it was set explicitly."""
        if _env("SPECORA_LLM_TIMEOUT"):
            return policy
        return replace(policy, timeout_seconds=OLLAMA_DEFAULT_TIMEOUT_SECONDS)

    @staticmethod
    def _ollama_capabilities(model_id: str, registry: ModelRegistry) -> ModelCapabilities:
        """Return capabilities for an Ollama tag, registering unknown ones.

        Users pull arbitrary tags, so an unrecognised name is normal rather
        than an error — but it gets the minimal capability set, since the only
        honest thing to say about an unknown model is that we do not know.
        """
        caps = registry.get(model_id)
        if caps is not None and caps.provider == "ollama":
            return caps
        registry.register(model_id, UNKNOWN_OLLAMA_MODEL)
        return UNKNOWN_OLLAMA_MODEL

    @classmethod
    def _from_explicit_model(
        cls, model_id: str, registry: ModelRegistry, policy: RetryPolicy
    ) -> EngineConfig:
        """Resolve a config for a caller-pinned ``SPECORA_AI_MODEL``."""
        ollama_url = _env("OLLAMA_BASE_URL")
        caps = registry.get(model_id)

        if caps is None:
            if not ollama_url:
                raise EngineConfigError(f"Unknown model: {model_id}")
            caps = cls._ollama_capabilities(model_id, registry)

        if caps.provider == "ollama":
            return cls._build(
                model_id,
                registry,
                cls._ollama_policy(policy),
                base_url=ollama_url or None,
                capabilities=caps,
            )

        if caps.provider == "anthropic":
            return cls._build(
                model_id,
                registry,
                policy,
                api_key=_env("ANTHROPIC_API_KEY"),
                capabilities=caps,
            )

        if caps.provider == "zai":
            return cls._build(
                model_id,
                registry,
                policy,
                api_key=_env("ZAI_API_KEY"),
                capabilities=caps,
            )

        if caps.provider == "openai":
            api_key, base_url = cls._resolve_openai_compatible(model_id)
            return cls._build(
                model_id,
                registry,
                policy,
                api_key=api_key,
                base_url=base_url,
                capabilities=caps,
            )

        return cls._build(model_id, registry, policy, capabilities=caps)

    @staticmethod
    def _resolve_openai_compatible(model_id: str) -> tuple[str | None, str | None]:
        """Pick the key and base URL for an OpenAI-wire-format model.

        Three vendors share this provider, so the model ID decides which
        credential applies before falling back to whichever key is present.
        """
        if model_id.startswith("grok"):
            return _env("XAI_API_KEY") or None, XAI_BASE_URL
        if model_id.startswith("gemini"):
            return _env("GOOGLE_API_KEY") or None, GOOGLE_BASE_URL

        openai_key = _env("OPENAI_API_KEY")
        if openai_key:
            return openai_key, None
        xai_key = _env("XAI_API_KEY")
        if xai_key:
            return xai_key, XAI_BASE_URL
        google_key = _env("GOOGLE_API_KEY")
        if google_key:
            return google_key, GOOGLE_BASE_URL
        return None, None

    @classmethod
    def _build(
        cls,
        model_id: str,
        registry: ModelRegistry,
        policy: RetryPolicy,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> EngineConfig:
        """Assemble an ``EngineConfig``, looking up capabilities if needed."""
        caps = capabilities or registry.get(model_id)
        if caps is None:
            raise EngineConfigError(f"Unknown model: {model_id}")
        return cls(
            model_id=model_id,
            capabilities=caps,
            api_key=api_key or None,
            base_url=base_url,
            strategy=caps.best_strategy(),
            retry_policy=policy,
        )

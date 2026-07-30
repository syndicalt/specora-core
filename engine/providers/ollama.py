"""Ollama provider — local models over Ollama's OpenAI-compatible endpoint.

Ollama serves ``/v1/chat/completions`` with OpenAI's wire format, so this is
the OpenAI provider pointed at a local host with the auth requirement dropped:
the server ignores the bearer token but the SDK refuses to construct a client
without one, hence the placeholder key.

Local models are a first-class target for Specora because contract healing
sends whole contracts to the model, and a self-hosted endpoint is the only
configuration where that never leaves the machine.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.providers.base import LLMResponse, Message, ToolDefinition
from engine.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

# The SDK validates that a key is present; Ollama never reads it.
_PLACEHOLDER_KEY = "ollama"


def normalize_base_url(raw: str | None) -> str:
    """Return an OpenAI-compatible base URL for an Ollama host.

    Accepts what users actually set -- ``http://localhost:11434``,
    a trailing slash, or the full ``/v1`` path -- and always yields the
    ``/v1`` form the SDK needs.
    """
    base = (raw or DEFAULT_OLLAMA_BASE_URL).strip().rstrip("/")
    if not base:
        base = DEFAULT_OLLAMA_BASE_URL
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


class OllamaProvider(OpenAIProvider):
    """Provider for a local Ollama server."""

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """Initialise against an Ollama host.

        Args:
            model: Ollama model tag, e.g. ``llama3.3:70b``.
            base_url: Ollama host; defaults to ``http://localhost:11434``.
            timeout: Per-request timeout in seconds. Local generation on a
                large model is slow, so callers should raise this well above
                the hosted-provider default.
        """
        super().__init__(
            api_key=_PLACEHOLDER_KEY,
            model=model,
            base_url=normalize_base_url(base_url),
            timeout=timeout,
        )

    def provider_name(self) -> str:
        """Return ``'ollama'``."""
        return "ollama"

    def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat request to the local Ollama server.

        Tool definitions are dropped rather than forwarded: tool support in
        Ollama varies per model, and a silently ignored ``tools`` argument
        produces a plausible-looking text reply where the caller expected a
        tool call. Dropping it loudly keeps the failure visible.
        """
        if tools:
            logger.warning(
                "Ollama model %s: dropping %d tool definition(s); tool use is "
                "not advertised for local models in the registry",
                self._model,
                len(tools),
            )
        return super().chat(
            messages,
            system=system,
            tools=None,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
        )

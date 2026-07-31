"""Provider base classes — the abstract interface every LLM provider implements.

Concrete providers (Anthropic, OpenAI, Z.AI, Ollama) subclass ``Provider`` and
translate these neutral data structures into SDK-specific calls.

Providers own transport concerns only: authentication, message translation,
and a per-request timeout. They must not retry internally -- the SDK retry
counters are pinned to zero and ``engine.retry`` is the single place that
decides whether a failure is worth resending. Two independent retry loops
multiply into 9 requests where the operator asked for 3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """A single message in a conversation."""

    role: str  # "user", "assistant", "tool"
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolDefinition:
    """Describes a tool the model can invoke."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the tool's input


@dataclass
class LLMResponse:
    """Normalised response from any provider."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class Provider(ABC):
    """Abstract base for LLM providers.

    Each provider translates ``Message`` / ``ToolDefinition`` into its
    SDK's native types, calls the API, and returns an ``LLMResponse``.
    """

    @abstractmethod
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
        """Send a chat completion request and return the response.

        Args:
            response_schema: JSON Schema for the expected reply. When given,
                the provider must engage its native structured-output mode so
                ``LLMResponse.content`` is a JSON document, not prose that
                happens to contain one.
        """
        ...

    def supports_native_structured_output(self) -> bool:
        """Whether ``response_schema`` engages a real provider-side guarantee.

        Providers that only nudge the model with a prompt return ``False`` so
        the engine knows the reply still needs defensive extraction.
        """
        return False

    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. ``'anthropic'``, ``'openai'``)."""
        ...

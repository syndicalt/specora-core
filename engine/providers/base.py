"""Provider base classes — the abstract interface every LLM provider implements.

Concrete providers (Anthropic, OpenAI, Ollama) subclass ``Provider`` and
translate these neutral data structures into SDK-specific calls.
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
    ) -> LLMResponse:
        """Send a chat completion request and return the response."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. ``'anthropic'``, ``'openai'``)."""
        ...

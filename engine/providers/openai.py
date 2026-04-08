"""OpenAI provider — translates neutral messages into OpenAI API calls.

Uses the ``openai`` Python SDK.  System messages are injected as the first
message in the conversation (OpenAI's chat completions expect this).

Also works with OpenAI-compatible endpoints (xAI, Google Gemini, etc.)
by accepting an optional ``base_url``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition

logger = logging.getLogger(__name__)


class OpenAIProvider(Provider):
    """Provider implementation for OpenAI and OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        """Initialise with an API key, model, and optional base URL.

        Args:
            api_key: OpenAI API key (``sk-...``).
            model: Model ID, e.g. ``gpt-4o``.
            base_url: Optional base URL for OpenAI-compatible endpoints
                      (e.g. ``https://api.x.ai/v1``).
        """
        try:
            import openai  # noqa: F811
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for the OpenAI provider. "
                "Install it with: pip install openai"
            ) from exc

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url

        self._client = openai.OpenAI(**kwargs)
        self._model = model

    def provider_name(self) -> str:
        """Return ``'openai'``."""
        return "openai"

    # ------------------------------------------------------------------
    # Message conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_messages(
        messages: list[Message],
        system: str | None = None,
    ) -> list[dict[str, Any]]:
        """Convert neutral ``Message`` objects to OpenAI's message format.

        OpenAI expects the system prompt as the first message with
        ``role: "system"``.  Tool calls and tool results use specific
        fields in the message dict.
        """
        converted: list[dict[str, Any]] = []

        if system:
            converted.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == "tool":
                # Each tool result becomes a separate tool message
                for result in msg.tool_results:
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": result.get("tool_use_id", ""),
                            "content": str(result.get("content", "")),
                        }
                    )

            elif msg.tool_calls:
                # Assistant message with tool calls
                openai_tool_calls = [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(tc.get("input", {})),
                        },
                    }
                    for tc in msg.tool_calls
                ]
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": openai_tool_calls,
                }
                converted.append(entry)

            else:
                converted.append({"role": msg.role, "content": msg.content})

        return converted

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert neutral ``ToolDefinition`` objects to OpenAI's function format.

        OpenAI expects::

            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    # ------------------------------------------------------------------
    # Core chat method
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion request to the OpenAI API.

        Args:
            messages: Conversation history as neutral ``Message`` objects.
            system: Optional system prompt (prepended as system message).
            tools: Optional tool definitions the model may invoke.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            Normalised ``LLMResponse`` with content, tool calls, and usage.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._convert_messages(messages, system=system),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        logger.debug("OpenAI request: model=%s, messages=%d", self._model, len(messages))
        response = self._client.chat.completions.create(**kwargs)

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Parse an OpenAI API response into a normalised ``LLMResponse``.

        OpenAI returns a single choice with an assistant message that may
        contain text content and/or tool calls.
        """
        choice = response.choices[0]
        message = choice.message

        content = message.content or ""
        tool_calls: list[dict[str, Any]] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": json.loads(tc.function.arguments),
                    }
                )

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        # Map OpenAI finish_reason to our stop_reason
        stop_reason = choice.finish_reason or ""
        if stop_reason == "stop":
            stop_reason = "end_turn"
        elif stop_reason == "tool_calls":
            stop_reason = "tool_use"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )

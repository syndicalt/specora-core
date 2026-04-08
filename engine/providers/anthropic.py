"""Anthropic provider — translates neutral messages into Claude API calls.

Uses the ``anthropic`` Python SDK.  System messages are passed separately
(Anthropic's API takes ``system`` as a top-level parameter, not as a message).
"""
from __future__ import annotations

import logging
from typing import Any

from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition

logger = logging.getLogger(__name__)


class AnthropicProvider(Provider):
    """Provider implementation for Anthropic's Claude models."""

    def __init__(self, api_key: str, model: str) -> None:
        """Initialise with an API key and model identifier.

        Args:
            api_key: Anthropic API key (``sk-ant-...``).
            model: Model ID, e.g. ``claude-sonnet-4-6``.
        """
        try:
            import anthropic  # noqa: F811
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for the Anthropic provider. "
                "Install it with: pip install anthropic"
            ) from exc

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def provider_name(self) -> str:
        """Return ``'anthropic'``."""
        return "anthropic"

    # ------------------------------------------------------------------
    # Message conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert neutral ``Message`` objects to Anthropic's message format.

        Anthropic expects::

            {"role": "user" | "assistant", "content": str | list[block]}

        Tool results are sent as ``role: "user"`` with ``tool_result`` blocks.
        """
        converted: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "tool":
                # Tool results go as user messages with tool_result content blocks
                blocks: list[dict[str, Any]] = []
                for result in msg.tool_results:
                    blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": result.get("tool_use_id", ""),
                            "content": str(result.get("content", "")),
                        }
                    )
                converted.append({"role": "user", "content": blocks})

            elif msg.tool_calls:
                # Assistant message with tool calls
                blocks = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "input": tc.get("input", {}),
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})

            else:
                converted.append({"role": msg.role, "content": msg.content})

        return converted

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert neutral ``ToolDefinition`` objects to Anthropic's tool format.

        Anthropic expects::

            {"name": str, "description": str, "input_schema": JSONSchema}
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
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
        """Send a chat completion request to the Anthropic API.

        Args:
            messages: Conversation history as neutral ``Message`` objects.
            system: Optional system prompt (passed as top-level param).
            tools: Optional tool definitions the model may invoke.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            Normalised ``LLMResponse`` with content, tool calls, and usage.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._convert_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if system:
            kwargs["system"] = system

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        logger.debug("Anthropic request: model=%s, messages=%d", self._model, len(messages))
        response = self._client.messages.create(**kwargs)

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Parse an Anthropic API response into a normalised ``LLMResponse``.

        Anthropic responses contain a list of content blocks that can be
        ``text`` or ``tool_use`` blocks.  We extract both.
        """
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return LLMResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            usage=usage,
        )

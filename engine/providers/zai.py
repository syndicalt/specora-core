"""Z.AI provider — OpenAI-compatible with JWT-signed authentication.

Z.AI (chat.z.ai / Zhipu BigModel) uses a unique auth scheme: the API key
is formatted as `{key_id}.{secret}`, and requests must include a JWT token
signed with the secret — not the raw key.

This provider extends the OpenAI provider by signing a fresh JWT before
each request.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition

logger = logging.getLogger(__name__)

ZAI_BASE_URL = "https://api.z.ai/api/paas/v4/"


def sign_zai_token(api_key: str) -> str:
    """Sign a JWT token from a Z.AI API key.

    The key format is `{key_id}.{secret}`. The JWT payload includes
    the key_id, expiration, and timestamp. Signed with HS256.

    Args:
        api_key: Z.AI API key in `{id}.{secret}` format.

    Returns:
        Signed JWT token string.
    """
    try:
        import jwt
    except ImportError:
        raise ImportError(
            "The 'pyjwt' package is required for Z.AI. "
            "Install it with: pip install pyjwt"
        )

    parts = api_key.split(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid Z.AI API key format. Expected 'key_id.secret', "
            f"got {len(parts)} part(s)."
        )

    key_id, secret = parts

    payload = {
        "api_key": key_id,
        "exp": int(time.time()) + 300,  # 5 minutes
        "timestamp": int(time.time()),
    }

    token = jwt.encode(
        payload,
        secret,
        algorithm="HS256",
        headers={"alg": "HS256", "sign_type": "SIGN"},
    )

    return token


class ZAIProvider(Provider):
    """Provider for Z.AI (Zhipu BigModel) with JWT-signed auth."""

    def provider_name(self) -> str:
        return "zai"

    def __init__(self, api_key: str, model: str) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for the Z.AI provider. "
                "Install it with: pip install openai"
            ) from exc

        self._api_key = api_key
        self._model = model
        self._openai = openai

    def _get_client(self):
        """Create a fresh client with a newly signed JWT token."""
        token = sign_zai_token(self._api_key)
        return self._openai.OpenAI(api_key=token, base_url=ZAI_BASE_URL)

    def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat request to Z.AI with JWT-signed auth."""
        # Build messages
        oai_messages: list[dict[str, Any]] = []
        if system:
            oai_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == "tool" and msg.tool_results:
                for tr in msg.tool_results:
                    oai_messages.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": tr.get("content", ""),
                    })
            elif msg.role == "assistant" and msg.tool_calls:
                oai_messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": (
                                    tc.get("input", "{}")
                                    if isinstance(tc.get("input"), str)
                                    else __import__("json").dumps(tc.get("input", {}))
                                ),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })
            else:
                oai_messages.append({"role": msg.role, "content": msg.content})

        # Build kwargs
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": oai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        # Sign fresh JWT and make request
        client = self._get_client()
        response = client.chat.completions.create(**kwargs)

        # Parse response
        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = __import__("json").loads(tc.function.arguments)
                except Exception:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            usage={
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

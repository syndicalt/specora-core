"""Structured-output helpers — get a JSON object out of a model reliably.

The preferred path is the provider's own structured-output mode (declared by
``ModelCapabilities.supports_structured_output``), which makes the response a
JSON document by construction. This module is the fallback for models that
lack it, and the parser for the modes that return JSON as text.

The naive approach -- ``re.search(r'\\{[^}]+\\}', text)`` -- is wrong twice
over: ``[^}]+`` cannot span a nested object, so it truncates at the first
inner brace, and it matches the first brace group anywhere in the reply,
including one quoted inside prose. :func:`extract_json_object` instead scans
for a *balanced* object while tracking string literals and escapes, and tries
candidates in order.

A parse failure raises :class:`StructuredOutputError` carrying the raw text.
Callers must not swallow it: "the model returned prose" and "the model
returned an object with the wrong shape" need different responses, and a
generic error string erases the difference.
"""
from __future__ import annotations

import json
import re
from typing import Any


def schema_instruction(system: str | None, schema: dict[str, Any]) -> str:
    """Append *schema* to a system prompt for JSON-mode providers.

    OpenAI-compatible JSON mode only guarantees syntactically valid JSON; it
    says nothing about the keys. Showing the model the schema is what makes
    the shape likely. The literal word "JSON" must also appear in the prompt
    or the API rejects the request outright.
    """
    preamble = (system or "").rstrip()
    schema_text = json.dumps(schema, indent=2)
    return (
        f"{preamble}\n\n"
        f"Respond with a single JSON object conforming to this JSON Schema:\n"
        f"{schema_text}"
    ).strip()


_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n(?P<body>.*?)\n?```",
    re.DOTALL,
)

_MAX_ECHO = 500


class StructuredOutputError(ValueError):
    """Raised when a model reply cannot be read as the requested JSON object."""

    def __init__(self, message: str, raw: str) -> None:
        excerpt = raw if len(raw) <= _MAX_ECHO else raw[:_MAX_ECHO] + "…"
        super().__init__(f"{message} Raw response: {excerpt!r}")
        self.raw = raw


def _balanced_object_spans(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of every top-level balanced ``{...}``.

    Braces inside string literals are ignored, so an object containing
    ``"note": "use { carefully }"`` still yields one span.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    spans.append((start, i + 1))
                    start = -1

    return spans


def _candidates(text: str) -> list[str]:
    """Return decreasingly-literal readings of *text* to try as JSON."""
    seen: set[str] = set()
    out: list[str] = []

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    add(text)
    for match in _FENCE_RE.finditer(text):
        add(match.group("body"))
    for start, end in _balanced_object_spans(text):
        add(text[start:end])
    # A fenced block may itself wrap prose around the object.
    for match in _FENCE_RE.finditer(text):
        body = match.group("body")
        for start, end in _balanced_object_spans(body):
            add(body[start:end])

    return out


def extract_json_object(text: str) -> dict:
    """Return the JSON object embedded in *text*.

    Raises:
        StructuredOutputError: If no candidate parses as a JSON object.
    """
    if not text or not text.strip():
        raise StructuredOutputError("Model returned an empty response.", text or "")

    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise StructuredOutputError(
        "No JSON object found in the model response.", text
    )

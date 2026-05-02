"""Generated-file provenance helpers."""

from __future__ import annotations

import re

SPECORA_SOURCE_KEY = "Specora-Source"

_SOURCE_LINE = re.compile(rf"{SPECORA_SOURCE_KEY}:\s*(.+)")
_LEGACY_GENERATED_FROM = re.compile(r"@generated\s+from\s+([\w/.-]+)")
_FQN = re.compile(r"^(entity|workflow|page|route|agent|mixin|infra|domain)/[\w/.-]+$")


def provenance_source_line(provenance: str) -> str:
    """Return the machine-readable provenance payload for a generated file."""
    return f"{SPECORA_SOURCE_KEY}: {provenance}"


def parse_provenance_sources(text: str) -> list[str]:
    """Extract source contract FQNs from generated-file text.

    Supports the canonical `Specora-Source:` header and the older
    `@generated from ...` line used by a few generated files.
    """
    for line in text.splitlines():
        match = _SOURCE_LINE.search(line)
        if not match:
            continue
        sources = [part.strip() for part in match.group(1).split(",")]
        return [source for source in sources if _FQN.match(source)]

    match = _LEGACY_GENERATED_FROM.search(text)
    if match:
        source = match.group(1)
        if _FQN.match(source):
            return [source]

    return []


def first_provenance_source(text: str) -> str | None:
    """Return the first parseable provenance source from generated-file text."""
    sources = parse_provenance_sources(text)
    return sources[0] if sources else None

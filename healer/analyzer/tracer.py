"""Runtime stacktrace → contract FQN inference."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_GENERATED_PATTERN = re.compile(r"@generated\s+from\s+([\w/]+)")


def trace_to_contract(
    stacktrace: str,
    context: dict,
    domains_root: Path = Path("domains"),
) -> Optional[str]:
    if context.get("contract_fqn"):
        return context["contract_fqn"]

    generated_file = context.get("generated_file")
    if generated_file:
        fqn = _read_generated_header(Path(generated_file))
        if fqn:
            return fqn

    for match in re.finditer(r'File "([^"]*runtime[^"]*)"', stacktrace):
        fqn = _read_generated_header(Path(match.group(1)))
        if fqn:
            return fqn

    return None


def _read_generated_header(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:500]
        match = _GENERATED_PATTERN.search(head)
        return match.group(1) if match else None
    except OSError:
        return None

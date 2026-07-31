"""Filesystem safety for contract emission.

Every path the Factory writes is derived from a name that arrived from an LLM
tool call, an LLM-authored YAML document, or a command line. None of those are
trusted, so a name is a directory-traversal vector until it has been checked
against the same pattern the envelope meta-schema enforces on
``metadata.name``. ``normalize_name`` only fixes *casing* — it passes ``..``,
``/`` and unicode straight through — so it is not a sanitizer and must never
be used alone to build a path.

Writes go through :func:`write_atomic` so a crash or a full disk cannot
leave a half-written contract behind. Contracts are the declared source of
truth; a truncated one is worse than an absent one because it still parses.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from forge.normalize import normalize_name

# Mirrors metadata.name / metadata.domain in spec/meta/envelope.meta.yaml. A
# name that fails validation there must never have reached a path anyway.
CONTRACT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

KIND_SUBDIRS = {
    "entity": "entities",
    "workflow": "workflows",
    "route": "routes",
    "page": "pages",
    "agent": "agents",
    "mixin": "mixins",
    "infra": "infra",
}


class UnsafeNameError(ValueError):
    """Raised when a name cannot be used as a contract identifier or filename."""


def safe_name(raw: object, *, what: str = "name") -> str:
    """Normalize *raw* to snake_case and reject anything still unusable.

    Args:
        raw: The candidate name. Non-strings are rejected rather than coerced,
            because ``str(dict)`` would produce a plausible-looking name from
            a malformed LLM response.
        what: Label used in the error message (e.g. ``"entity name"``).

    Returns:
        The normalized name, guaranteed to match :data:`CONTRACT_NAME_RE`.

    Raises:
        UnsafeNameError: If the normalized name is not a legal identifier.
    """
    if not isinstance(raw, str):
        raise UnsafeNameError(f"{what} must be a string, got {type(raw).__name__}")
    normalized = normalize_name(raw)
    if not CONTRACT_NAME_RE.fullmatch(normalized):
        # Spelled out rather than quoting CONTRACT_NAME_RE: this message is
        # printed through Rich, which would read the pattern's `[a-z]` as
        # markup and swallow it.
        raise UnsafeNameError(
            f"{what} {raw!r} is not a legal contract name (normalized to "
            f"{normalized!r}; must start with a lower-case letter and contain "
            "only lower-case letters, digits and underscores)"
        )
    return normalized


def contract_path(base: Path, domain: str, kind: str, name: str) -> Path:
    """Build the on-disk path for a contract, refusing to escape *base*.

    Args:
        base: The contracts root (typically ``domains/``).
        domain: Domain name; validated with :func:`safe_name`.
        kind: Lowercase contract kind (``entity``, ``route``, ...).
        name: Contract name; validated with :func:`safe_name`.

    Raises:
        UnsafeNameError: If a component is illegal, the kind is unknown, or the
            resolved path would land outside *base*.
    """
    subdir = KIND_SUBDIRS.get(kind)
    if subdir is None:
        raise UnsafeNameError(f"unknown contract kind {kind!r}")

    safe_domain = safe_name(domain, what="domain")
    safe_stem = safe_name(name, what=f"{kind} name")
    path = base / safe_domain / subdir / f"{safe_stem}.contract.yaml"

    # Belt and braces: safe_name already excludes separators, but the base
    # itself can be a symlink or a relative path, and containment is the
    # property that actually matters.
    root = base.resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise UnsafeNameError(f"contract path {path} escapes {base}")
    return path


def write_atomic(path: Path, content: str) -> None:
    """Write *content* to *path* in one indivisible step.

    The temp file is created in the destination directory so ``os.replace``
    stays within a filesystem, and is fsynced before the swap so an interrupted
    write cannot leave the contract name pointing at unwritten blocks.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

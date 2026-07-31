"""Versioned prompt registry.

Specora's thesis is that specifications are the source of truth, so the
prompts that read and mutate those specifications cannot themselves be
untracked string literals scattered through call sites. Every managed prompt
lives in :mod:`engine.prompts.library` with an explicit name, an integer
version, a description, and a content checksum, and is looked up by name at
the call site.

The one rule: **prompt bodies are immutable once registered**. Changing model
behaviour means adding version *n+1*, not editing version *n* in place. That
is what makes "which prompt produced this contract edit?" answerable after
the fact -- the checksum recorded alongside a change still resolves.

Usage::

    from engine.prompts import get_prompt

    prompt = get_prompt("cli_router")          # latest version
    system = prompt.render(commands=table)
    print(prompt.ref)                          # cli_router@v1:0f3c...
"""

from __future__ import annotations

from engine.prompts import library  # noqa: F401  (import populates the registry)
from engine.prompts.registry import (
    Prompt,
    PromptNotFoundError,
    PromptRegistry,
    get_prompt,
    register,
    registry,
)

__all__ = [
    "Prompt",
    "PromptNotFoundError",
    "PromptRegistry",
    "get_prompt",
    "register",
    "registry",
]

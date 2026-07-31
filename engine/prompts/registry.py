"""Prompt registry types — name, version, checksum, and lookup.

The prompt bodies themselves live in :mod:`engine.prompts.library`; this
module holds only the machinery. See :mod:`engine.prompts` for the rationale
and the public entry points.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from string import Template


class PromptNotFoundError(KeyError):
    """Raised when a prompt name or version is not registered."""


@dataclass(frozen=True)
class Prompt:
    """One immutable, versioned prompt body."""

    name: str
    version: int
    description: str
    body: str

    @property
    def checksum(self) -> str:
        """Short SHA-256 of the body — the identity recorded with an edit."""
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:16]

    @property
    def ref(self) -> str:
        """Stable reference such as ``cli_router@v1:0f3c...``."""
        return f"{self.name}@v{self.version}:{self.checksum}"

    def render(self, **variables: object) -> str:
        """Substitute ``$name`` placeholders in the body.

        Uses :class:`string.Template` rather than ``str.format`` because
        prompt bodies routinely contain literal JSON braces.
        """
        if not variables:
            return self.body
        return Template(self.body).safe_substitute(
            {k: str(v) for k, v in variables.items()}
        )


class PromptRegistry:
    """Name + version lookup over registered prompts."""

    def __init__(self) -> None:
        self._by_name: dict[str, dict[int, Prompt]] = {}

    def register(self, prompt: Prompt) -> Prompt:
        """Register *prompt*.

        Raises:
            ValueError: If that name/version is already registered with a
                different body — the immutability rule, enforced.
        """
        versions = self._by_name.setdefault(prompt.name, {})
        existing = versions.get(prompt.version)
        if existing is not None and existing.body != prompt.body:
            raise ValueError(
                f"Prompt {prompt.name}@v{prompt.version} is already registered "
                f"with a different body. Register a new version instead."
            )
        versions[prompt.version] = prompt
        return prompt

    def get(self, name: str, version: int | None = None) -> Prompt:
        """Return a prompt by name, defaulting to the highest version."""
        versions = self._by_name.get(name)
        if not versions:
            raise PromptNotFoundError(f"No prompt registered under {name!r}")
        if version is None:
            version = max(versions)
        prompt = versions.get(version)
        if prompt is None:
            known = ", ".join(f"v{v}" for v in sorted(versions))
            raise PromptNotFoundError(
                f"Prompt {name!r} has no version {version} (known: {known})"
            )
        return prompt

    def history(self, name: str) -> list[Prompt]:
        """Return every registered version of *name*, oldest first."""
        versions = self._by_name.get(name)
        if not versions:
            raise PromptNotFoundError(f"No prompt registered under {name!r}")
        return [versions[v] for v in sorted(versions)]

    def list_prompts(self) -> list[Prompt]:
        """Return the latest version of every registered prompt, by name."""
        return [self.get(name) for name in sorted(self._by_name)]


_registry = PromptRegistry()


def registry() -> PromptRegistry:
    """Return the process-wide prompt registry."""
    return _registry


def register(prompt: Prompt) -> Prompt:
    """Register *prompt* in the process-wide registry."""
    return _registry.register(prompt)


def get_prompt(name: str, version: int | None = None) -> Prompt:
    """Look up a prompt in the process-wide registry."""
    return _registry.get(name, version)

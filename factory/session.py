"""Factory session persistence — save and resume interview state.

A ``Session`` manages the lifecycle of a Factory interview, persisting
state to ``.factory/session.json`` relative to a root directory. This
allows long-running, multi-turn domain discovery conversations to survive
process restarts.

Typical flow::

    session = Session(root=Path("."))
    if session.can_resume():
        session.resume()
    else:
        session.start("library", "A book-lending system")

    session.add_entity("book", {"fields": ["title", "isbn"]})
    session.add_message("user", "What about authors?")
    session.save()

When the interview is complete, call ``session.cleanup()`` to remove the
persisted state file.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionError(Exception):
    """Raised when a session operation fails."""


@dataclass
class SessionState:
    """Holds all mutable state for a Factory interview session.

    Attributes:
        domain: The domain being modelled (e.g. ``"library"``).
        description: Free-text description of the domain.
        phase: Current interview phase (e.g. ``"domain_discovery"``).
        entities_discovered: Ordered list of entity names found so far.
        current_entity: Name of the entity currently being interviewed, or ``None``.
        entity_data: Mapping of entity name to interview data captured for it.
        workflow_data: Mapping of workflow name to interview data captured for it.
        conversation_history: Ordered list of ``{"role": ..., "content": ...}`` dicts.
        created_at: ISO-8601 timestamp when the session was started.
        updated_at: ISO-8601 timestamp of the last mutation.
    """

    domain: str = ""
    description: str = ""
    phase: str = ""
    entities_discovered: list[str] = field(default_factory=list)
    current_entity: str | None = None
    entity_data: dict[str, Any] = field(default_factory=dict)
    workflow_data: dict[str, Any] = field(default_factory=dict)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the session state to a plain dict suitable for JSON.

        Returns:
            A JSON-safe dictionary containing all session fields.
        """
        return {
            "domain": self.domain,
            "description": self.description,
            "phase": self.phase,
            "entities_discovered": list(self.entities_discovered),
            "current_entity": self.current_entity,
            "entity_data": dict(self.entity_data),
            "workflow_data": dict(self.workflow_data),
            "conversation_history": list(self.conversation_history),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        """Deserialize a session state from a plain dict.

        Args:
            data: Dictionary previously produced by :meth:`to_dict`.

        Returns:
            A new ``SessionState`` instance with the restored values.
        """
        return cls(
            domain=data.get("domain", ""),
            description=data.get("description", ""),
            phase=data.get("phase", ""),
            entities_discovered=data.get("entities_discovered", []),
            current_entity=data.get("current_entity"),
            entity_data=data.get("entity_data", {}),
            workflow_data=data.get("workflow_data", {}),
            conversation_history=data.get("conversation_history", []),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


class Session:
    """Manages Factory interview state persistence.

    State is stored as JSON at ``{root}/.factory/session.json``.

    Args:
        root: Directory that anchors the session file. Defaults to the
            current working directory.
    """

    SESSION_DIR = ".factory"
    SESSION_FILE = "session.json"

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else Path.cwd()
        self._session_path = self._root / self.SESSION_DIR / self.SESSION_FILE
        self.state = SessionState()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, domain: str, description: str) -> None:
        """Start a new interview session.

        Initializes session state with the given domain and description,
        sets the phase to ``"domain_discovery"``, and records timestamps.

        Args:
            domain: Name of the domain being modelled.
            description: Human-readable description of the domain.

        Raises:
            SessionError: If a session is already in progress (session file exists).
        """
        now = datetime.now(timezone.utc).isoformat()
        self.state = SessionState(
            domain=domain,
            description=description,
            phase="domain_discovery",
            created_at=now,
            updated_at=now,
        )
        logger.info("Started new session for domain '%s'", domain)

    def can_resume(self) -> bool:
        """Check whether a persisted session file exists.

        Returns:
            ``True`` if ``.factory/session.json`` exists under the root directory.
        """
        return self._session_path.exists()

    def resume(self) -> None:
        """Load session state from disk.

        Reads ``.factory/session.json`` and restores ``self.state``.

        Raises:
            SessionError: If the session file does not exist or cannot be parsed.
        """
        if not self._session_path.exists():
            raise SessionError(
                f"No session file found at {self._session_path}. "
                "Use start() to begin a new session."
            )

        try:
            raw = self._session_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            self.state = SessionState.from_dict(data)
            logger.info("Resumed session for domain '%s'", self.state.domain)
        except (json.JSONDecodeError, KeyError) as exc:
            raise SessionError(f"Failed to parse session file: {exc}") from exc

    def save(self) -> None:
        """Persist the current session state to disk as JSON.

        Creates the ``.factory/`` directory if it does not already exist
        and writes ``session.json`` with an updated ``updated_at`` timestamp.

        Raises:
            SessionError: If the file cannot be written.
        """
        self.state.updated_at = datetime.now(timezone.utc).isoformat()

        try:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.state.to_dict(), indent=2)
            self._session_path.write_text(payload, encoding="utf-8")
            logger.debug("Session saved to %s", self._session_path)
        except OSError as exc:
            raise SessionError(f"Failed to save session: {exc}") from exc

    def cleanup(self) -> None:
        """Remove the persisted session file.

        Safe to call even if the file has already been removed.
        """
        try:
            self._session_path.unlink(missing_ok=True)
            # Remove the .factory directory if it is now empty.
            if self._session_path.parent.exists() and not any(
                self._session_path.parent.iterdir()
            ):
                self._session_path.parent.rmdir()
            logger.info("Session cleaned up")
        except OSError as exc:
            raise SessionError(f"Failed to cleanup session: {exc}") from exc

    # ------------------------------------------------------------------
    # Data accumulation
    # ------------------------------------------------------------------

    def add_entity(self, name: str, data: dict[str, Any]) -> None:
        """Record entity interview data.

        Adds the entity name to ``entities_discovered`` (if not already
        present) and stores the data under ``entity_data[name]``.

        Args:
            name: Entity name (e.g. ``"book"``).
            data: Arbitrary dict of interview data for this entity.
        """
        if name not in self.state.entities_discovered:
            self.state.entities_discovered.append(name)
        self.state.entity_data[name] = data
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        logger.debug("Added entity '%s'", name)

    def add_workflow(self, name: str, data: dict[str, Any]) -> None:
        """Record workflow interview data.

        Args:
            name: Workflow name (e.g. ``"book_lifecycle"``).
            data: Arbitrary dict of interview data for this workflow.
        """
        self.state.workflow_data[name] = data
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        logger.debug("Added workflow '%s'", name)

    def add_message(self, role: str, content: str) -> None:
        """Append a message to the conversation history.

        Args:
            role: Message role (e.g. ``"user"``, ``"assistant"``).
            content: The message text.
        """
        self.state.conversation_history.append({"role": role, "content": content})
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        logger.debug("Added %s message (%d chars)", role, len(content))

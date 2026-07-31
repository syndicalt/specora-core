"""Tests for Factory session persistence — save, resume, cleanup.

All tests use ``tmp_path`` for filesystem isolation so nothing leaks
between runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from factory.session import Session, SessionError


class TestSessionCreate:
    """Verify that starting a session sets the expected initial state."""

    def test_session_create(self, tmp_path: Path) -> None:
        """Start a session, verify domain/description/phase are correct."""
        session = Session(root=tmp_path)
        session.start(domain="library", description="A book-lending system")

        assert session.state.domain == "library"
        assert session.state.description == "A book-lending system"
        assert session.state.phase == "domain_discovery"


class TestSessionSaveAndLoad:
    """Verify round-trip persistence: save then load preserves all state."""

    def test_session_save_and_load(self, tmp_path: Path) -> None:
        """Save a session with entities and messages, resume it, verify all state preserved."""
        session = Session(root=tmp_path)
        session.start(domain="itsm", description="Incident management")
        session.add_entity("incident", {"fields": ["title", "severity"]})
        session.add_workflow("incident_lifecycle", {"states": ["new", "active", "resolved"]})
        session.add_message("user", "What entities do we need?")
        session.add_message("assistant", "Let's start with Incident.")
        session.save()

        # Load in a fresh Session instance
        loaded = Session(root=tmp_path)
        assert loaded.can_resume()
        loaded.resume()

        assert loaded.state.domain == "itsm"
        assert loaded.state.description == "Incident management"
        assert loaded.state.phase == "domain_discovery"
        assert loaded.state.entity_data == {"incident": {"fields": ["title", "severity"]}}
        assert loaded.state.workflow_data == {
            "incident_lifecycle": {"states": ["new", "active", "resolved"]}
        }
        assert len(loaded.state.conversation_history) == 2
        assert loaded.state.conversation_history[0] == {
            "role": "user",
            "content": "What entities do we need?",
        }
        assert loaded.state.created_at is not None
        assert loaded.state.updated_at is not None


class TestSessionCleanup:
    """Verify cleanup removes the session file."""

    def test_session_cleanup(self, tmp_path: Path) -> None:
        """Save then cleanup, verify can_resume() is False."""
        session = Session(root=tmp_path)
        session.start(domain="library", description="test")
        session.save()
        assert session.can_resume()

        session.cleanup()
        assert not session.can_resume()


class TestSessionAddEntityData:
    """Verify entity data accumulates correctly."""

    def test_session_add_entity_data(self, tmp_path: Path) -> None:
        """Add entity, verify it appears in entity_data dict."""
        session = Session(root=tmp_path)
        session.start(domain="library", description="test")

        session.add_entity("book", {"fields": ["title", "isbn"]})
        session.add_entity("author", {"fields": ["name"]})

        assert "book" in session.state.entity_data
        assert session.state.entity_data["book"] == {"fields": ["title", "isbn"]}
        assert "author" in session.state.entity_data
        assert session.state.entity_data["author"] == {"fields": ["name"]}
        assert "book" in session.state.entities_discovered
        assert "author" in session.state.entities_discovered


class TestSessionAddWorkflowData:
    """Verify workflow data accumulates correctly."""

    def test_session_add_workflow_data(self, tmp_path: Path) -> None:
        """Add workflow, verify it appears in workflow_data dict."""
        session = Session(root=tmp_path)
        session.start(domain="library", description="test")

        session.add_workflow("book_lifecycle", {"states": ["available", "checked_out"]})

        assert "book_lifecycle" in session.state.workflow_data
        assert session.state.workflow_data["book_lifecycle"] == {
            "states": ["available", "checked_out"]
        }


class TestSessionDurability:
    """The session is saved after every interview answer, including on Ctrl-C."""

    def test_failed_save_leaves_the_previous_session_intact(self, tmp_path: Path) -> None:
        session = Session(root=tmp_path)
        session.start(domain="library", description="first")
        session.save()

        session.state.description = "second"

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        original_replace = os.replace
        os.replace = boom
        try:
            with pytest.raises(SessionError):
                session.save()
        finally:
            os.replace = original_replace

        reloaded = Session(root=tmp_path)
        reloaded.resume()
        assert reloaded.state.description == "first"

    def test_resume_rejects_a_non_object_session_file(self, tmp_path: Path) -> None:
        path = tmp_path / Session.SESSION_DIR / Session.SESSION_FILE
        path.parent.mkdir(parents=True)
        path.write_text('["not", "an", "object"]', encoding="utf-8")

        with pytest.raises(SessionError, match="not an object"):
            Session(root=tmp_path).resume()

    def test_resume_reports_a_corrupt_session_file(self, tmp_path: Path) -> None:
        path = tmp_path / Session.SESSION_DIR / Session.SESSION_FILE
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(SessionError, match="Failed to parse"):
            Session(root=tmp_path).resume()

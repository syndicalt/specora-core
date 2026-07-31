"""Tests for the editor preview round-trip."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from factory.preview import editor

CONTRACTS = {
    "entities/book.contract.yaml": "kind: Entity\n",
    "routes/books.contract.yaml": "kind: Route\n",
}


@pytest.fixture(autouse=True)
def _no_editor(monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)


class TestTerminalPreview:
    def test_accept_returns_the_contracts_unchanged(self, monkeypatch) -> None:
        monkeypatch.setattr(editor.console, "input", lambda *_a, **_k: "")
        accepted, files = editor.preview_contracts(CONTRACTS)
        assert accepted
        assert files == CONTRACTS

    def test_decline_reports_rejection(self, monkeypatch) -> None:
        monkeypatch.setattr(editor.console, "input", lambda *_a, **_k: "n")
        accepted, _ = editor.preview_contracts(CONTRACTS)
        assert not accepted


class TestEditorPreview:
    def test_edits_are_read_back(self, monkeypatch) -> None:
        def fake_run(argv, check=True):
            root = Path(argv[-1])
            target = root / "entities" / "book.contract.yaml"
            target.write_text("kind: Entity\nedited: true\n", encoding="utf-8")

        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(editor.console, "input", lambda *_a, **_k: "y")

        accepted, files = editor.preview_contracts(CONTRACTS)
        assert accepted
        assert "edited: true" in files["entities/book.contract.yaml"]

    def test_deleted_file_is_reported_not_just_logged(self, monkeypatch, capsys) -> None:
        def fake_run(argv, check=True):
            (Path(argv[-1]) / "routes" / "books.contract.yaml").unlink()

        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(editor.console, "input", lambda *_a, **_k: "y")

        _, files = editor.preview_contracts(CONTRACTS)
        assert "routes/books.contract.yaml" not in files
        # Deleting a route can orphan its entity, so the user has to see it
        # before confirming.
        assert "routes/books.contract.yaml" in capsys.readouterr().out

    def test_a_broken_editor_falls_back_to_the_terminal(self, monkeypatch) -> None:
        monkeypatch.setenv("EDITOR", "definitely-not-an-editor")

        def boom(*_a, **_k):
            raise FileNotFoundError("definitely-not-an-editor")

        monkeypatch.setattr(subprocess, "run", boom)
        monkeypatch.setattr(editor.console, "input", lambda *_a, **_k: "y")

        accepted, files = editor.preview_contracts(CONTRACTS)
        assert accepted
        assert files == CONTRACTS

    def test_editor_with_flags_is_split_not_shelled_out(self, monkeypatch) -> None:
        seen: list[list[str]] = []

        def fake_run(argv, check=True):
            seen.append(argv)

        monkeypatch.setenv("EDITOR", "code --wait")
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(editor.console, "input", lambda *_a, **_k: "y")

        editor.preview_contracts(CONTRACTS)
        assert seen and seen[0][:2] == ["code", "--wait"]

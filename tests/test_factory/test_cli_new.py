"""Tests for `specora factory new`'s emit phase.

The command turns accumulated session state into a whole domain on disk. What
matters is that the set it produces compiles as a unit — a per-contract check
cannot see a route pointing at an entity that was never emitted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.cli.new import _emit_domain, _validate_emitted_contracts
from factory.emitters.base import EmitterError
from factory.paths import UnsafeNameError, write_atomic
from factory.session import Session
from forge.ir.compiler import Compiler


def _session(tmp_path: Path) -> Session:
    session = Session(root=tmp_path)
    session.start("library", "A book-lending system")
    session.add_entity(
        "book",
        {
            "description": "A book",
            "fields": {
                "title": {"type": "string", "required": True},
                "price": {"type": "decimal", "constraints": {"precision": 10, "scale": 2}},
                "isbn": {"type": "string", "sensitive": True},
            },
            "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
            "state_machine": "workflow/library/book_lifecycle",
        },
    )
    session.add_entity(
        "shelf",
        {
            "description": "A shelf",
            "fields": {"code": {"type": "string", "required": True}},
            "mixins": ["mixin/stdlib/identifiable"],
        },
    )
    session.add_workflow(
        "book_lifecycle",
        {
            "initial": "available",
            "states": {
                "available": {"label": "Available", "category": "open"},
                "lent": {"label": "Lent", "category": "hold"},
                "lost": {"label": "Lost", "category": "closed", "terminal": True},
            },
            "transitions": {"available": ["lent", "lost"], "lent": ["available", "lost"]},
        },
    )
    return session


class TestEmitDomain:
    def test_emitted_domain_validates_and_compiles(self, tmp_path: Path) -> None:
        contracts = _emit_domain("library", _session(tmp_path))
        assert _validate_emitted_contracts(contracts) == []

        out = tmp_path / "out"
        for rel_path, content in contracts.items():
            write_atomic(out / rel_path, content)

        ir = Compiler(contract_root=out).compile()
        assert {e.name for e in ir.entities} == {"book", "shelf"}
        assert len(ir.routes) == 2
        assert len(ir.pages) == 2

    def test_plurals_come_from_the_shared_pluralizer(self, tmp_path: Path) -> None:
        session = Session(root=tmp_path)
        session.start("shop", "")
        session.add_entity(
            "class_room",
            {"fields": {"code": {"type": "string"}}, "mixins": ["mixin/stdlib/identifiable"]},
        )
        contracts = _emit_domain("shop", session)
        # `name + "s"` produced "class_rooms" here but "addresss" elsewhere.
        assert "routes/class_rooms.contract.yaml" in contracts

    def test_write_only_field_is_kept_off_the_page(self, tmp_path: Path) -> None:
        contracts = _emit_domain("library", _session(tmp_path))
        assert "isbn" not in contracts["pages/books.contract.yaml"]
        assert "title" in contracts["pages/books.contract.yaml"]

    def test_unusable_entity_name_stops_the_emit(self, tmp_path: Path) -> None:
        session = Session(root=tmp_path)
        session.start("shop", "")
        session.add_entity("../../etc/passwd", {"fields": {"a": {"type": "string"}}})
        with pytest.raises(UnsafeNameError):
            _emit_domain("shop", session)

    def test_invalid_interview_data_stops_the_emit(self, tmp_path: Path) -> None:
        session = Session(root=tmp_path)
        session.start("shop", "")
        session.add_entity("widget", {"fields": {"a": {"type": "not_a_type"}}})
        with pytest.raises(EmitterError):
            _emit_domain("shop", session)


class TestPostEditValidation:
    def test_an_edit_that_breaks_a_contract_is_caught(self, tmp_path: Path) -> None:
        # `preview_contracts` opens $EDITOR and reads the files back, so what
        # gets written is not what was validated before the preview.
        contracts = _emit_domain("library", _session(tmp_path))
        edited = dict(contracts)
        edited["entities/book.contract.yaml"] = contracts["entities/book.contract.yaml"].replace(
            "type: string", "type: strng"
        )

        errors = _validate_emitted_contracts(edited)
        assert errors and any("strng" in e.message for e in errors)

    def test_an_edit_that_breaks_yaml_is_caught(self, tmp_path: Path) -> None:
        contracts = _emit_domain("library", _session(tmp_path))
        edited = dict(contracts)
        edited["entities/book.contract.yaml"] = "spec:\n\tfields: {"

        errors = _validate_emitted_contracts(edited)
        assert errors and "Invalid YAML" in errors[0].message

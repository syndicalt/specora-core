from __future__ import annotations

from forge.diff.change_contract import build_change_contract
from forge.diff.models import Compatibility, DiffOrigin, FieldChange
from forge.diff.tracker import create_diff


def test_added_optional_field_is_backward_compatible() -> None:
    cc = build_change_contract([
        FieldChange(
            path="spec.fields.nickname",
            old_value=None,
            new_value={"type": "string"},
            change_type="added",
        )
    ])

    assert cc.compatibility == Compatibility.BACKWARD_COMPATIBLE
    assert cc.migration_required is False
    assert cc.destructive is False
    assert cc.affected_surfaces == ["database"]
    assert "run generated backend tests" in cc.verification


def test_removed_field_is_destructive_and_requires_migration() -> None:
    cc = build_change_contract([
        FieldChange(
            path="spec.fields.legacy_code",
            old_value={"type": "string"},
            new_value=None,
            change_type="removed",
        )
    ])

    assert cc.compatibility == Compatibility.DESTRUCTIVE
    assert cc.migration_required is True
    assert cc.destructive is True
    assert cc.notes == ["Destructive change at spec.fields.legacy_code"]


def test_workflow_guard_change_is_behavioral() -> None:
    cc = build_change_contract([
        FieldChange(
            path="spec.guards.new -> done.require_fields[0]",
            old_value=None,
            new_value="resolution",
            change_type="added",
        )
    ])

    assert cc.compatibility == Compatibility.BEHAVIORAL
    assert cc.migration_required is False
    assert cc.affected_surfaces == ["workflow"]
    assert "run workflow transition tests" in cc.verification


def test_create_diff_attaches_change_contract() -> None:
    before = {
        "kind": "Entity",
        "spec": {"fields": {"name": {"type": "string"}}},
    }
    after = {
        "kind": "Entity",
        "spec": {"fields": {"name": {"type": "string"}, "age": {"type": "integer"}}},
    }

    diff = create_diff(
        contract_fqn="entity/test/person",
        before=before,
        after=after,
        origin=DiffOrigin.HUMAN,
        reason="Add age",
    )

    assert diff.change_contract is not None
    assert diff.change_contract.compatibility == Compatibility.BACKWARD_COMPATIBLE
    assert diff.change_contract.affected_surfaces == ["database"]

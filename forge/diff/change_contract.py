"""Classify contract diffs into durable change contracts."""

from __future__ import annotations

from forge.diff.models import ChangeContract, Compatibility, FieldChange


def build_change_contract(changes: list[FieldChange]) -> ChangeContract:
    """Build a change contract from field-level changes."""
    affected: set[str] = set()
    verification: set[str] = set()
    notes: list[str] = []

    migration_required = False
    destructive = False
    behavioral = False

    for change in changes:
        surface = _affected_surface(change.path)
        if surface:
            affected.add(surface)

        if _is_destructive(change):
            destructive = True
            migration_required = True
            notes.append(f"Destructive change at {change.path}")

        if _requires_migration(change):
            migration_required = True

        if _is_behavioral(change):
            behavioral = True

    if "database" in affected:
        verification.add("compile DomainIR")
        verification.add("generate and review migration")
        verification.add("run generated backend tests")
    if "api" in affected:
        verification.add("run generated API tests")
    if "frontend" in affected:
        verification.add("run generated frontend checks")
    if "workflow" in affected:
        verification.add("run workflow transition tests")
    if "agent" in affected:
        verification.add("validate agent input/output fields")

    if destructive:
        compatibility = Compatibility.DESTRUCTIVE
    elif migration_required:
        compatibility = Compatibility.MIGRATION_REQUIRED
    elif behavioral:
        compatibility = Compatibility.BEHAVIORAL
    else:
        compatibility = Compatibility.BACKWARD_COMPATIBLE

    if not verification:
        verification.add("validate contract schema")

    return ChangeContract(
        compatibility=compatibility,
        migration_required=migration_required,
        destructive=destructive,
        affected_surfaces=sorted(affected) or ["contract"],
        verification=sorted(verification),
        notes=notes,
    )


def _affected_surface(path: str) -> str:
    if path.startswith("spec.fields"):
        return "database"
    if path.startswith("spec.endpoints") or path.startswith("spec.global_behaviors"):
        return "api"
    if path.startswith("spec.views") or path.startswith("spec.sections") or path.startswith("spec.actions"):
        return "frontend"
    if path.startswith("spec.transitions") or path.startswith("spec.guards") or path.startswith("spec.side_effects"):
        return "workflow"
    if path.startswith("spec.input") or path.startswith("spec.output") or path.startswith("spec.ai_integration"):
        return "agent"
    if path.startswith("metadata"):
        return "documentation"
    return "contract"


def _is_destructive(change: FieldChange) -> bool:
    if change.change_type == "removed":
        return change.path.startswith("spec.fields") or change.path.startswith("spec.endpoints")
    if change.change_type == "type_changed":
        return change.path.startswith("spec.fields")
    return False


def _requires_migration(change: FieldChange) -> bool:
    if not change.path.startswith("spec.fields"):
        return False
    if change.change_type in {"removed", "type_changed"}:
        return True
    if change.path.endswith(".type") or change.path.endswith(".required") or change.path.endswith(".default"):
        return True
    return False


def _is_behavioral(change: FieldChange) -> bool:
    return (
        change.path.startswith("spec.transitions")
        or change.path.startswith("spec.guards")
        or change.path.startswith("spec.side_effects")
        or change.path.startswith("spec.validation")
        or change.path.startswith("spec.ai_integration")
    )

"""Semantic validation for compiled Forge IR.

JSON Schema validation checks contract shape. Semantic validation checks
cross-contract meaning after the compiler has normalized contracts into IR.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.ir.model import DomainIR, EntityIR, MixinIR, StateMachineIR
from forge.targets.naming import class_name, module_slug


@dataclass(frozen=True)
class SemanticValidationError:
    """A semantic contract error found in the compiled IR."""

    contract_fqn: str
    path: str
    message: str
    severity: str = "error"


def validate_semantics(ir: DomainIR) -> list[SemanticValidationError]:
    """Validate cross-contract semantics in a compiled domain.

    Args:
        ir: The compiled DomainIR after IR passes have run.

    Returns:
        A list of semantic validation errors. Empty means the IR is coherent
        enough for generators to consume.
    """
    errors: list[SemanticValidationError] = []

    entity_map = {e.fqn: e for e in ir.entities}
    mixin_map = {m.fqn: m for m in ir.mixins}
    workflow_map = {w.fqn: w for w in ir.workflows}

    for entity in ir.entities:
        errors.extend(_validate_entity_semantics(entity, entity_map, mixin_map, workflow_map))

    for workflow in ir.workflows:
        errors.extend(_validate_workflow_semantics(workflow))

    for page in ir.pages:
        if page.entity_fqn and page.entity_fqn not in entity_map:
            errors.append(
                SemanticValidationError(
                    contract_fqn=page.fqn,
                    path="spec.entity",
                    message=f"Page references missing entity '{page.entity_fqn}'",
                )
            )

    errors.extend(_validate_identifier_uniqueness(ir))

    for route in ir.routes:
        entity = entity_map.get(route.entity_fqn)
        if route.entity_fqn and entity is None:
            errors.append(
                SemanticValidationError(
                    contract_fqn=route.fqn,
                    path="spec.entity",
                    message=f"Route references missing entity '{route.entity_fqn}'",
                )
            )
            continue

        if entity is not None:
            field_names = {f.name for f in entity.fields}
            for idx, endpoint in enumerate(route.endpoints):
                for field_name in endpoint.required_fields:
                    if field_name not in field_names:
                        errors.append(
                            SemanticValidationError(
                                contract_fqn=route.fqn,
                                path=f"spec.endpoints[{idx}].request_body.required_fields",
                                message=(
                                    f"Endpoint requires missing field '{field_name}' "
                                    f"on entity '{entity.fqn}'"
                                ),
                            )
                        )

    return errors


def _validate_identifier_uniqueness(ir: DomainIR) -> list[SemanticValidationError]:
    """Reject builds where two entities would generate the same identifier.

    Every generator derives its output names from the entity — the Python
    class, the SQL table, the route module filename. If two entities produce
    the same one, the result is not an error anywhere downstream; it is a
    silent overwrite:

      * duplicate class in `models.py` -> Python keeps the last definition,
        so one entity's request validation is replaced by the other's;
      * duplicate `CREATE TABLE IF NOT EXISTS` -> the second is a no-op, so one
        entity reads and writes a table that lacks its columns;
      * duplicate module path -> one entity's entire API disappears.

    Catching this here means a colliding build fails to compile with a message
    naming both entities, instead of deploying and corrupting data.
    """
    errors: list[SemanticValidationError] = []
    multi = ir.multi_domain

    def _check(kind: str, path: str, derive) -> None:
        claims: dict[str, str] = {}
        for entity in ir.entities:
            value = derive(entity)
            if value in claims:
                errors.append(
                    SemanticValidationError(
                        contract_fqn=entity.fqn,
                        path=path,
                        message=(
                            f"{kind} '{value}' is claimed by both "
                            f"'{claims[value]}' and '{entity.fqn}'. Generated "
                            f"output would silently overwrite one with the "
                            f"other. Rename one entity, or set an explicit "
                            f"distinct 'table' in its spec."
                        ),
                    )
                )
            else:
                claims[value] = entity.fqn

    _check("Table name", "spec.table", lambda e: e.table_name)
    _check(
        "Generated class name",
        "metadata.name",
        lambda e: class_name(e.name, e.domain, multi_domain=multi),
    )
    _check(
        "Generated module name",
        "metadata.name",
        lambda e: module_slug(e.name, e.domain, multi_domain=multi),
    )

    return errors


def _validate_mixin_field_conflicts(
    entity: EntityIR,
    mixin_map: dict[str, MixinIR],
) -> list[SemanticValidationError]:
    """Reject an entity field that shadows a mixin field of a different type.

    Redeclaring a mixin's field is legitimate — an entity may want a tighter
    description, a different default, extra constraints. Redeclaring it with a
    different *type* is not: the mixin exists precisely so that every entity
    carrying it presents the same shape, and the entity's version wins during
    expansion. An entity declaring `created_at: string` against
    mixin/stdlib/timestamped's `created_at: datetime` therefore produced a TEXT
    column, a `str` on the Pydantic model and an ISO-string in the API, while
    every consumer of the mixin — ordering, retention, the `now_on_update`
    computation — assumes a timestamp. Nothing downstream can detect that; the
    types are individually valid everywhere they land.

    Runs after mixin_expansion, which is why the entity's own type is the one
    still present on the field: expansion skips a mixin field whose name is
    already taken.
    """
    errors: list[SemanticValidationError] = []
    fields_by_name = {f.name: f for f in entity.fields}

    for ref in entity.mixin_refs:
        mixin = mixin_map.get(ref)
        if mixin is None:
            continue  # Reported separately as a missing-mixin reference.

        for mixin_field in mixin.fields:
            entity_field = fields_by_name.get(mixin_field.name)
            if entity_field is None or entity_field.type == mixin_field.type:
                continue

            errors.append(
                SemanticValidationError(
                    contract_fqn=entity.fqn,
                    path=f"spec.fields.{mixin_field.name}.type",
                    message=(
                        f"Field '{mixin_field.name}' is declared as "
                        f"'{entity_field.type}' but mixin '{ref}' defines it as "
                        f"'{mixin_field.type}'. The entity's type wins on "
                        f"expansion, which would silently change the shape the "
                        f"mixin guarantees. Use the mixin's type, or rename the "
                        f"field, or drop the mixin."
                    ),
                )
            )

    return errors


def _validate_entity_semantics(
    entity: EntityIR,
    entity_map: dict[str, EntityIR],
    mixin_map: dict[str, MixinIR],
    workflow_map: dict[str, StateMachineIR],
) -> list[SemanticValidationError]:
    errors: list[SemanticValidationError] = []
    field_names = {f.name for f in entity.fields}

    for ref in entity.mixin_refs:
        if ref not in mixin_map:
            errors.append(
                SemanticValidationError(
                    contract_fqn=entity.fqn,
                    path="spec.mixins",
                    message=f"Entity references missing mixin '{ref}'",
                )
            )

    errors.extend(_validate_mixin_field_conflicts(entity, mixin_map))

    workflow_ref = entity.workflow_ref
    if workflow_ref and workflow_ref not in workflow_map:
        errors.append(
            SemanticValidationError(
                contract_fqn=entity.fqn,
                path="spec.state_machine",
                message=f"Entity references missing workflow '{workflow_ref}'",
            )
        )

    for field in entity.fields:
        if not field.reference or not field.reference.target_entity:
            continue

        target = entity_map.get(field.reference.target_entity)
        if target is None:
            errors.append(
                SemanticValidationError(
                    contract_fqn=entity.fqn,
                    path=f"spec.fields.{field.name}.references.entity",
                    message=(
                        f"Field '{field.name}' references missing entity "
                        f"'{field.reference.target_entity}'"
                    ),
                )
            )
            continue

        target_fields = {f.name for f in target.fields}
        if field.reference.display_field not in target_fields:
            errors.append(
                SemanticValidationError(
                    contract_fqn=entity.fqn,
                    path=f"spec.fields.{field.name}.references.display",
                    message=(
                        f"Field '{field.name}' displays missing field "
                        f"'{field.reference.display_field}' on entity '{target.fqn}'"
                    ),
                )
            )

    if entity.state_machine:
        for guard in entity.state_machine.guards:
            for field_name in guard.require_fields:
                if field_name not in field_names:
                    errors.append(
                        SemanticValidationError(
                            contract_fqn=entity.fqn,
                            path="spec.state_machine.guards.require_fields",
                            message=(
                                f"Workflow guard '{guard.from_state} -> {guard.to_state}' "
                                f"requires missing field '{field_name}' on entity '{entity.fqn}'"
                            ),
                        )
                    )

    return errors


def _validate_workflow_semantics(workflow: StateMachineIR) -> list[SemanticValidationError]:
    errors: list[SemanticValidationError] = []
    state_names = {s.name for s in workflow.states}

    if workflow.initial not in state_names:
        errors.append(
            SemanticValidationError(
                contract_fqn=workflow.fqn,
                path="spec.initial",
                message=f"Workflow initial state '{workflow.initial}' is not declared",
            )
        )

    for source, targets in workflow.transitions.items():
        if source not in state_names:
            errors.append(
                SemanticValidationError(
                    contract_fqn=workflow.fqn,
                    path=f"spec.transitions.{source}",
                    message=f"Workflow transition source '{source}' is not declared",
                )
            )
        for target in targets:
            if target not in state_names:
                errors.append(
                    SemanticValidationError(
                        contract_fqn=workflow.fqn,
                        path=f"spec.transitions.{source}",
                        message=f"Workflow transition target '{target}' is not declared",
                    )
                )

    transition_pairs = {
        (source, target) for source, targets in workflow.transitions.items() for target in targets
    }
    for guard in workflow.guards:
        if (guard.from_state, guard.to_state) not in transition_pairs:
            errors.append(
                SemanticValidationError(
                    contract_fqn=workflow.fqn,
                    path="spec.guards",
                    message=(
                        f"Workflow guard '{guard.from_state} -> {guard.to_state}' "
                        "does not match a declared transition"
                    ),
                )
            )

    return errors

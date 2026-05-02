"""Semantic validation for compiled Forge IR.

JSON Schema validation checks contract shape. Semantic validation checks
cross-contract meaning after the compiler has normalized contracts into IR.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.ir.model import DomainIR, EntityIR, StateMachineIR


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
    mixin_fqns = {m.fqn for m in ir.mixins}
    workflow_map = {w.fqn: w for w in ir.workflows}

    for entity in ir.entities:
        errors.extend(_validate_entity_semantics(entity, entity_map, mixin_fqns, workflow_map))

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


def _validate_entity_semantics(
    entity: EntityIR,
    entity_map: dict[str, EntityIR],
    mixin_fqns: set[str],
    workflow_map: dict[str, StateMachineIR],
) -> list[SemanticValidationError]:
    errors: list[SemanticValidationError] = []
    field_names = {f.name for f in entity.fields}

    for ref in getattr(entity, "_mixin_refs", []):
        if ref not in mixin_fqns:
            errors.append(
                SemanticValidationError(
                    contract_fqn=entity.fqn,
                    path="spec.mixins",
                    message=f"Entity references missing mixin '{ref}'",
                )
            )

    workflow_ref = getattr(entity, "_workflow_ref", None)
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
        (source, target)
        for source, targets in workflow.transitions.items()
        for target in targets
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

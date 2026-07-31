"""State machine binding pass — binds workflow contracts to entities.

When an entity declares `state_machine: "workflow/library/book_lifecycle"`,
this pass finds the corresponding StateMachineIR from the compiled
workflows and attaches it to the entity.

It also ensures the entity has a `state` field with the correct enum
values from the workflow's states.
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR, FieldIR

logger = logging.getLogger(__name__)


def bind_state_machines(ir: DomainIR) -> DomainIR:
    """Bind workflow contracts to entities that reference them.

    For each entity with a `workflow_ref`, find the matching
    StateMachineIR and attach a private copy of it. Also ensure the entity
    has a `state` field with the valid states as enum values.

    Args:
        ir: The DomainIR to process.

    Returns:
        The DomainIR with state machines bound to entities.
    """
    # Build workflow lookup by FQN
    workflow_map = {w.fqn: w for w in ir.workflows}

    for entity in ir.entities:
        workflow_ref = entity.workflow_ref
        if not workflow_ref:
            continue

        workflow = workflow_map.get(workflow_ref)
        if workflow is None:
            # Reported as a hard error by validate_semantics; the pass just has
            # nothing to bind.
            logger.debug(
                "Entity '%s' references workflow '%s' which was not found",
                entity.fqn,
                workflow_ref,
            )
            continue

        # Deep copy, not the shared instance. One workflow is routinely bound to
        # several entities and also stays in `ir.workflows`, so assigning the
        # same object made every one of them the same object: a generator that
        # appended an entity-specific state, or a pass that rewrote a guard,
        # would silently rewrite the workflow for every other entity too. This
        # matches mixin_expansion, which already copies each field it grafts on;
        # the two passes previously disagreed on whether the IR was shared.
        entity.state_machine = workflow.model_copy(deep=True)
        logger.debug("Bound workflow '%s' to entity '%s'", workflow_ref, entity.fqn)

        # Ensure entity has a state field with correct enum values
        state_names = [s.name for s in workflow.states]
        existing_state = next((f for f in entity.fields if f.name == "state"), None)

        if existing_state:
            # Update enum values from the workflow
            existing_state.enum_values = state_names
            if not existing_state.default:
                existing_state.default = workflow.initial
        else:
            # Add a state field
            entity.fields.append(
                FieldIR(
                    name="state",
                    type="string",
                    description="Lifecycle state (managed by workflow)",
                    required=False,
                    default=workflow.initial,
                    enum_values=state_names,
                )
            )

    return ir

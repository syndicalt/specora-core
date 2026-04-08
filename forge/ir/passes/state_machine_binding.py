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

    For each entity with a _workflow_ref, find the matching
    StateMachineIR and attach it. Also ensure the entity has
    a `state` field with the valid states as enum values.

    Args:
        ir: The DomainIR to process.

    Returns:
        The DomainIR with state machines bound to entities.
    """
    # Build workflow lookup by FQN
    workflow_map = {w.fqn: w for w in ir.workflows}

    for entity in ir.entities:
        workflow_ref = getattr(entity, "_workflow_ref", None)
        if not workflow_ref:
            continue

        workflow = workflow_map.get(workflow_ref)
        if workflow is None:
            logger.warning(
                "Entity '%s' references workflow '%s' which was not found",
                entity.fqn, workflow_ref,
            )
            continue

        # Bind the state machine
        entity.state_machine = workflow
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

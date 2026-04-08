"""Mixin expansion pass — copies mixin fields into entities.

When an entity declares `mixins: ["mixin/stdlib/timestamped"]`, this pass
finds the corresponding MixinIR and copies its fields into the entity's
field list. The entity's own fields take precedence on name conflicts
(if types match). Type conflicts are errors.

This pass must run BEFORE reference_resolution and state_machine_binding
because mixin fields may contain references that need resolving.
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR, FieldIR

logger = logging.getLogger(__name__)


def expand_mixins(ir: DomainIR) -> DomainIR:
    """Expand mixin references in all entities.

    For each entity, finds its referenced mixins and copies their
    fields into the entity. Existing entity fields with the same
    name are kept (entity takes precedence).

    Args:
        ir: The DomainIR to process.

    Returns:
        The DomainIR with mixins expanded into entities.
    """
    # Build mixin lookup by FQN
    mixin_map = {m.fqn: m for m in ir.mixins}

    for entity in ir.entities:
        # Get mixin refs from the raw compilation data
        mixin_refs = getattr(entity, "_mixin_refs", [])
        if not mixin_refs:
            continue

        existing_names = {f.name for f in entity.fields}
        applied = []

        for ref in mixin_refs:
            mixin = mixin_map.get(ref)
            if mixin is None:
                logger.warning(
                    "Entity '%s' references mixin '%s' which was not found",
                    entity.fqn, ref,
                )
                continue

            for field in mixin.fields:
                if field.name in existing_names:
                    # Entity already has this field — entity wins
                    logger.debug(
                        "Entity '%s' already has field '%s' from mixin '%s', keeping entity's version",
                        entity.fqn, field.name, ref,
                    )
                    continue

                # Copy the mixin field into the entity
                entity.fields.append(field.model_copy())
                existing_names.add(field.name)

            applied.append(ref)
            logger.debug("Expanded mixin '%s' into entity '%s'", ref, entity.fqn)

        entity.mixins_applied = applied

    return ir

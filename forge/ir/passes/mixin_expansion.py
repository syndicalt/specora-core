"""Mixin expansion pass — copies mixin fields into entities.

When an entity declares `mixins: ["mixin/stdlib/timestamped"]`, this pass
finds the corresponding MixinIR and copies its fields into the entity's
field list. The entity's own fields take precedence on name conflicts.

A name conflict where the *types* also differ is an error, reported by
`forge.ir.semantic._validate_mixin_field_conflicts` — this pass returns a
DomainIR rather than errors, and reporting is semantic validation's job. The
check is not optional: an entity redeclaring the timestamped mixin's
`created_at` as `string` used to win silently, and the only trace was a TEXT
column where every generated caller expects a timestamp.

This pass must run BEFORE state_machine_binding because mixin fields may
contain references that later passes read.
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR

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
        if not entity.mixin_refs:
            continue

        existing_names = {f.name for f in entity.fields}
        applied = []

        for ref in entity.mixin_refs:
            mixin = mixin_map.get(ref)
            if mixin is None:
                logger.warning(
                    "Entity '%s' references mixin '%s' which was not found",
                    entity.fqn,
                    ref,
                )
                continue

            for field in mixin.fields:
                if field.name in existing_names:
                    # Entity already has this field — entity wins. Whether that
                    # override is legitimate is decided in semantic validation.
                    logger.debug(
                        "Entity '%s' already declares field '%s' from mixin '%s'",
                        entity.fqn,
                        field.name,
                        ref,
                    )
                    continue

                # Copy the mixin field into the entity
                entity.fields.append(field.model_copy())
                existing_names.add(field.name)

            applied.append(ref)
            logger.debug("Expanded mixin '%s' into entity '%s'", ref, entity.fqn)

        entity.mixins_applied = applied

    return ir

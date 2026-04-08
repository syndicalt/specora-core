"""Reference resolution pass — validates all entity references resolve.

After mixin expansion, entities may have reference fields pointing
to other entities. This pass validates that every reference target
actually exists in the compiled IR.

It also infers base_path for Route contracts that don't have one,
and validates that Page contracts reference existing entities.
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR
from forge.ir.passes.table_name_inference import _pluralize

logger = logging.getLogger(__name__)


def resolve_references(ir: DomainIR) -> DomainIR:
    """Validate and resolve all cross-entity references.

    Checks:
    - All entity field references point to existing entities
    - All page entity references point to existing entities
    - All route entity references point to existing entities
    - Route base_path is inferred if not set

    Warnings are logged for unresolvable references (non-fatal).

    Args:
        ir: The DomainIR to process.

    Returns:
        The DomainIR (unchanged except for inferred base_paths).
    """
    entity_fqns = {e.fqn for e in ir.entities}

    # Validate entity field references
    for entity in ir.entities:
        for field in entity.fields:
            if field.reference and field.reference.target_entity:
                target = field.reference.target_entity
                if target not in entity_fqns:
                    logger.warning(
                        "Entity '%s' field '%s' references '%s' which does not exist",
                        entity.fqn, field.name, target,
                    )

    # Validate page entity references
    for page in ir.pages:
        if page.entity_fqn and page.entity_fqn not in entity_fqns:
            logger.warning(
                "Page '%s' references entity '%s' which does not exist",
                page.fqn, page.entity_fqn,
            )

    # Validate and infer route entity references + base paths
    for route in ir.routes:
        if route.entity_fqn and route.entity_fqn not in entity_fqns:
            logger.warning(
                "Route '%s' references entity '%s' which does not exist",
                route.fqn, route.entity_fqn,
            )

        # Infer base_path from entity name if not set
        if not route.base_path and route.entity_fqn:
            entity_name = route.entity_fqn.split("/")[-1]
            route.base_path = "/" + _pluralize(entity_name)
            logger.debug("Inferred base_path '%s' for route '%s'", route.base_path, route.fqn)

    return ir

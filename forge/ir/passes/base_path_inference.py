"""Base path inference pass — gives every Route a base_path.

A Route contract may omit `base_path`; when it does, it is derived from the
entity the route manages, so `entity/library/book` yields `/books`.

This pass replaces `reference_resolution`, which claimed to "validate all entity
references resolve" and logged warnings for unresolvable entity, page and route
references. Those warnings were dead weight: `validate_semantics` raises hard
errors for exactly the same three conditions, and it runs *after* every pass, so
each warning was immediately followed by a CompilationError carrying the same
information. Its docstring's promise that unresolvable references were
"non-fatal" was false. Inferring base_path was the only thing it genuinely did,
and that is all this pass does now.
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR
from forge.targets.naming import pluralize

logger = logging.getLogger(__name__)


def infer_base_paths(ir: DomainIR) -> DomainIR:
    """Set `base_path` on routes that do not declare one.

    Args:
        ir: The DomainIR to process.

    Returns:
        The DomainIR with `base_path` set on every route bound to an entity.
    """
    for route in ir.routes:
        if route.base_path or not route.entity_fqn:
            continue

        entity_name = route.entity_fqn.split("/")[-1]
        route.base_path = "/" + pluralize(entity_name)
        logger.debug("Inferred base_path '%s' for route '%s'", route.base_path, route.fqn)

    return ir

"""Table name inference pass — derives PostgreSQL table names from entity names.

If an entity doesn't explicitly set `table` in its spec, this pass
infers it by pluralizing the entity name (simple English pluralization).

Examples:
    "book"       -> "books"
    "incident"   -> "incidents"
    "category"   -> "categories"
    "status"     -> "statuses"
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR

logger = logging.getLogger(__name__)


def _pluralize(name: str) -> str:
    """Simple English pluralization for table names.

    Not a full NLP pluralizer — handles common patterns for
    database table naming. Falls back to appending 's'.
    """
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    if name.endswith(("s", "sh", "ch", "x", "z")):
        return name + "es"
    return name + "s"


def infer_table_names(ir: DomainIR) -> DomainIR:
    """Infer table names for entities that don't have explicit ones.

    Args:
        ir: The DomainIR to process.

    Returns:
        The DomainIR with table_name set on all entities.
    """
    for entity in ir.entities:
        if not entity.table_name:
            entity.table_name = _pluralize(entity.name)
            logger.debug("Inferred table name '%s' for entity '%s'", entity.table_name, entity.fqn)

    return ir

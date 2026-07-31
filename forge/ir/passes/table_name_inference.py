"""Table name inference pass — derives PostgreSQL table names from entities.

If an entity doesn't set `table` explicitly, its name is pluralized. In a
multi-domain build the domain is prefixed as well, because two entities named
`account` in different domains would otherwise both claim the table `accounts`
— and since the DDL generator emits `CREATE TABLE IF NOT EXISTS`, the second
statement would be a silent no-op rather than an error, leaving one entity
reading and writing another entity's table.

Examples (single-domain):
    "book"       -> "books"
    "incident"   -> "incidents"
    "category"   -> "categories"
    "status"     -> "statuses"

Examples (multi-domain):
    billing/account -> "billing_accounts"
    support/account -> "support_accounts"

Uniqueness is *enforced* in `forge.ir.semantic`, not here. Prefixing makes
collisions unlikely, but an explicit `table:` in a contract can still create
one, and that has to fail loudly rather than silently.

Pluralization itself lives in `forge.targets.naming` so the generators and this
pass cannot drift apart on what an entity's table is called.
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR
from forge.targets.naming import table_name as derive_table_name

logger = logging.getLogger(__name__)


def infer_table_names(ir: DomainIR) -> DomainIR:
    """Infer table names for entities that don't declare one explicitly.

    Args:
        ir: The DomainIR to process.

    Returns:
        The DomainIR with `table_name` set on every entity.
    """
    multi = ir.multi_domain

    for entity in ir.entities:
        if not entity.table_name:
            entity.table_name = derive_table_name(entity.name, entity.domain, multi_domain=multi)
            logger.debug(
                "Inferred table name '%s' for entity '%s'",
                entity.table_name,
                entity.fqn,
            )

    return ir

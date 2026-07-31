"""Which fields a contract declares filterable.

Three generators need this answer and they must give the same one: the DDL
generator builds a composite index per declared filter, the route generator
exposes each as a query parameter, and the test generator asserts on both. If
they disagree, the API offers a filter with no index behind it, or the database
carries an index nothing can ever use.

It lived as a private `_declared_filter_fields` inside the PostgreSQL target and
was imported through the underscore by the route generator. That import was the
right instinct — a second implementation is precisely the drift that produced
the defect this function now prevents — but reaching across a target boundary
for it left the authority in a module that has no claim to it. PostgreSQL is one
consumer of the answer, not its owner.
"""

from __future__ import annotations

from forge.ir.model import DomainIR


def declared_filter_fields(ir: DomainIR) -> dict[str, frozenset[str]]:
    """Collect, per entity FQN, the field names some contract declares filterable.

    Sources are the Page contract's `filters` block and its views' `filterable`
    lists, plus a Route contract's `global_behaviors.filters`.

    Everything is intersected with the entity's real field names. Those blocks
    also carry named saved-filter identifiers — `quick: [my_checkouts]` — which
    are labels for a stored query, not columns, and exposing one as a filter
    would emit a query parameter naming a column that does not exist.

    Args:
        ir: The compiled DomainIR.

    Returns:
        Entity FQN -> the set of its field names declared filterable. Every
        entity appears, with an empty set when nothing declares a filter for it.
    """
    fields_by_entity = {e.fqn: {f.name for f in e.fields} for e in ir.entities}
    declared: dict[str, set[str]] = {fqn: set() for fqn in fields_by_entity}

    def record(entity_fqn: str, candidates: object) -> None:
        if entity_fqn not in declared or not isinstance(candidates, list):
            return
        declared[entity_fqn].update(
            c for c in candidates if isinstance(c, str) and c in fields_by_entity[entity_fqn]
        )

    for page in ir.pages:
        for group in page.filters.values():
            record(page.entity_fqn, group)
        for view in page.views:
            if isinstance(view, dict):
                record(page.entity_fqn, view.get("filterable"))

    for route in ir.routes:
        record(route.entity_fqn, route.global_behaviors.get("filters"))

    return {fqn: frozenset(names) for fqn, names in declared.items()}

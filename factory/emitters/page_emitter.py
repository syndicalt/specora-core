"""Emit Page contract YAML from interview data."""

from __future__ import annotations

from factory.emitters.base import render


def page_columns(fields: dict) -> list[str]:
    """Pick the table columns for an entity's field definitions.

    Write-only fields are dropped: the API never serialises them, and the
    frontend generator rejects a page that names one (`_require_readable`),
    so listing a password hash here would break generation rather than render
    a blank column.

    Args:
        fields: The entity's ``spec.fields`` mapping.

    Returns:
        Field names safe to display, in declaration order.
    """
    return [
        name
        for name, definition in fields.items()
        if isinstance(definition, dict) and not definition.get("sensitive")
    ]


def emit_page(name: str, domain: str, entity_fqn: str, field_names: list[str]) -> str:
    """Convert interview data into a valid Page contract YAML string.

    Generates a mechanical (tier 1) page with a single table view whose
    columns are the first 6 names given.

    Every name in ``field_names`` must be a readable field on the target
    entity; the frontend generator refuses a page that names a field the
    entity does not have or that is write-only. Pass an empty list when the
    entity's fields are not known — the generator then falls back to the
    entity's own first six readable fields, which is always correct.

    Args:
        name: Page name (snake_case, typically pluralized entity name).
        domain: Domain namespace.
        entity_fqn: FQN of the entity this page displays.
        field_names: Readable field names on the entity. See :func:`page_columns`.

    Returns:
        Valid YAML string matching the Page meta-schema envelope.

    Raises:
        EmitterError: If the result fails the Page meta-schema.
    """
    table_columns = list(field_names[:6])

    views: list[dict] = [
        {
            "type": "table",
            "default": True,
            "columns": table_columns,
        },
    ]

    contract = {
        "apiVersion": "specora.dev/v1",
        "kind": "Page",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": f"Browse and manage {name}",
        },
        "requires": [entity_fqn],
        "spec": {
            "route": f"/{name}",
            "title": name.replace("_", " ").title(),
            "entity": entity_fqn,
            "generation_tier": "mechanical",
            "data_sources": [
                {"endpoint": f"/{name}", "alias": name},
            ],
            "views": views,
        },
    }

    return render(contract, what=f"page/{domain}/{name}")

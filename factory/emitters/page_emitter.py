"""Emit Page contract YAML from interview data."""
from __future__ import annotations

import yaml

from forge.normalize import normalize_contract


def emit_page(name: str, domain: str, entity_fqn: str, field_names: list[str]) -> str:
    """Convert interview data into a valid Page contract YAML string.

    Generates a mechanical (tier 1) page with table and kanban views.
    Table columns use the first 6 fields, kanban card_fields use the first 4.

    Args:
        name: Page name (snake_case, typically pluralized entity name).
        domain: Domain namespace.
        entity_fqn: FQN of the entity this page displays.
        field_names: List of field names available on the entity.

    Returns:
        Valid YAML string matching the Page meta-schema envelope.
    """
    table_columns = field_names[:6]

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

    normalize_contract(contract)

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)

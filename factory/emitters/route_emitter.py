"""Emit Route contract YAML from interview data."""
from __future__ import annotations

import yaml

from forge.normalize import normalize_contract


def emit_route(name: str, domain: str, entity_fqn: str, workflow_fqn: str = "") -> str:
    """Convert interview data into a valid Route contract YAML string.

    Auto-generates standard CRUD endpoints: GET list, POST create,
    GET by id, PATCH update, DELETE. If workflow_fqn is provided,
    adds a PUT /{id}/state endpoint.

    Args:
        name: Route name (snake_case, typically pluralized entity name).
        domain: Domain namespace.
        entity_fqn: FQN of the entity this route manages.
        workflow_fqn: Optional FQN of the workflow for state transitions.

    Returns:
        Valid YAML string matching the Route meta-schema envelope.
    """
    requires: list[str] = [entity_fqn]
    if workflow_fqn:
        requires.append(workflow_fqn)

    endpoints: list[dict] = [
        {
            "method": "GET",
            "path": "/",
            "summary": f"List all {name}",
            "response": {"status": 200, "shape": "list"},
        },
        {
            "method": "POST",
            "path": "/",
            "summary": f"Create a new {name.rstrip('s')}",
            "auto_fields": {"id": "uuid", "created_at": "now"},
            "response": {"status": 201, "shape": "entity"},
        },
        {
            "method": "GET",
            "path": "/{id}",
            "summary": f"Get a {name.rstrip('s')} by ID",
            "response": {"status": 200, "shape": "entity"},
        },
        {
            "method": "PATCH",
            "path": "/{id}",
            "summary": f"Update a {name.rstrip('s')}",
            "response": {"status": 200, "shape": "entity"},
        },
        {
            "method": "DELETE",
            "path": "/{id}",
            "summary": f"Delete a {name.rstrip('s')}",
            "response": {"status": 204},
        },
    ]

    if workflow_fqn:
        endpoints.append({
            "method": "PUT",
            "path": "/{id}/state",
            "summary": f"Transition {name.rstrip('s')} state",
            "request_body": {"required_fields": ["state"]},
            "response": {"status": 200, "shape": "entity"},
        })

    contract = {
        "apiVersion": "specora.dev/v1",
        "kind": "Route",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": f"CRUD API for {name}",
        },
        "requires": requires,
        "spec": {
            "entity": entity_fqn,
            "base_path": f"/{name}",
            "endpoints": endpoints,
        },
    }

    normalize_contract(contract)

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)

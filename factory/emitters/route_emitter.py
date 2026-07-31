"""Emit Route contract YAML from interview data."""

from __future__ import annotations

from factory.emitters.base import render


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

    Raises:
        EmitterError: If the result fails the Route meta-schema.
    """
    requires: list[str] = [entity_fqn]
    if workflow_fqn:
        requires.append(workflow_fqn)

    # The singular noun in the endpoint summaries is the entity's own name.
    # Deriving it from the route name instead (`name.rstrip("s")`) turned
    # "addresses" into "addresse" and "status" into "statu".
    singular = entity_fqn.rsplit("/", 1)[-1] or name

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
            "summary": f"Create a new {singular}",
            "auto_fields": {"id": "uuid", "created_at": "now"},
            "response": {"status": 201, "shape": "entity"},
        },
        {
            "method": "GET",
            "path": "/{id}",
            "summary": f"Get a {singular} by ID",
            "response": {"status": 200, "shape": "entity"},
        },
        {
            "method": "PATCH",
            "path": "/{id}",
            "summary": f"Update a {singular}",
            "response": {"status": 200, "shape": "entity"},
        },
        {
            "method": "DELETE",
            "path": "/{id}",
            "summary": f"Delete a {singular}",
            "response": {"status": 204},
        },
    ]

    if workflow_fqn:
        endpoints.append(
            {
                "method": "PUT",
                "path": "/{id}/state",
                "summary": f"Transition {singular} state",
                "request_body": {"required_fields": ["state"]},
                "response": {"status": 200, "shape": "entity"},
            }
        )

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

    return render(contract, what=f"route/{domain}/{name}")

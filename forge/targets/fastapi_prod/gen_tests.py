"""Generate black-box pytest tests from route contracts.

Generates a conftest.py (TestClient fixture, optional auth helpers) and one
test_{entity}.py per route contract.  Tests use DATABASE_BACKEND=memory so
they run with zero infrastructure.
"""
from __future__ import annotations

from typing import Any, Optional

from forge.ir.model import (
    DomainIR,
    EndpointIR,
    EntityIR,
    FieldIR,
    InfraIR,
    RouteIR,
    StateMachineIR,
)
from forge.targets.base import GeneratedFile, provenance_header


# =============================================================================
# Payload helpers
# =============================================================================

_TYPE_DEFAULTS: dict[str, str] = {
    "string": '"test"',
    "integer": "1",
    "number": "1.0",
    "boolean": "True",
    "text": '"test text"',
    "uuid": '"00000000-0000-0000-0000-000000000001"',
    "email": '"test@example.com"',
    "datetime": '"2024-01-01T00:00:00Z"',
    "date": '"2024-01-01"',
    "array": "[]",
    "object": "{}",
}


def _default_value(field: FieldIR) -> str:
    """Return a Python literal string suitable for embedding in generated code."""
    if field.enum_values:
        return repr(field.enum_values[0])
    if field.type == "integer":
        mn = field.constraints.get("min")
        return str(mn) if mn is not None else "1"
    if field.type == "number":
        mn = field.constraints.get("min")
        return str(float(mn)) if mn is not None else "1.0"
    return _TYPE_DEFAULTS.get(field.type, '"test"')


def _valid_payload_code(entity: EntityIR) -> str:
    """Return a dict literal string with valid field values for POST."""
    fields = [
        f for f in entity.fields
        if not f.computed and not f.immutable and f.name != "id"
    ]
    if not fields:
        return "{}"
    pairs = [f'    "{f.name}": {_default_value(f)}' for f in fields]
    return "{\n" + ",\n".join(pairs) + ",\n}"


def _required_fields(entity: EntityIR) -> list[FieldIR]:
    """Return required, user-supplied fields."""
    return [
        f for f in entity.fields
        if f.required and not f.computed and not f.immutable and f.name != "id"
    ]


# =============================================================================
# conftest.py generation
# =============================================================================


def _generate_conftest(ir: DomainIR) -> GeneratedFile:
    """Generate backend/tests/conftest.py."""
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    header = provenance_header("python", f"domain/{ir.domain}", "Test configuration and fixtures")

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import os",
        "import pytest",
        "",
        '# Force in-memory backend before any app imports',
        'os.environ["DATABASE_BACKEND"] = "memory"',
        'os.environ.setdefault("AUTH_SECRET", "test-secret")',
        f'os.environ.setdefault("AUTH_ENABLED", "{"true" if auth_infra else "false"}")',
        "",
        "from starlette.testclient import TestClient",
        "",
        "from backend.app import app",
        "",
        "",
        "# -- Markers ----------------------------------------------------------",
        "",
        "",
        "def pytest_configure(config):",
        '    config.addinivalue_line(',
        '        "markers",',
        '        "requires_pipeline: requires ML pipeline (skipped unless SPECORA_TEST_FULL=1)",',
        "    )",
        "",
        "",
        "def pytest_collection_modifyitems(config, items):",
        '    if os.environ.get("SPECORA_TEST_FULL"):',
        "        return",
        '    skip = pytest.mark.skip(reason="requires ML pipeline (set SPECORA_TEST_FULL=1)")',
        "    for item in items:",
        '        if "requires_pipeline" in item.keywords:',
        "            item.add_marker(skip)",
        "",
        "",
        "# -- Fixtures ---------------------------------------------------------",
        "",
        "",
        "@pytest.fixture",
        "def client():",
        '    """TestClient backed by the in-memory repository."""',
        "    with TestClient(app) as c:",
        "        yield c",
        "",
    ]

    if auth_infra:
        roles = auth_infra.config.get("roles", ["admin"])
        lines.extend([
            "",
            "# -- Auth helpers -----------------------------------------------------",
            "",
            "from datetime import datetime, timedelta, timezone",
            "from jose import jwt",
            "",
            'AUTH_SECRET = os.environ.get("AUTH_SECRET", "test-secret")',
            "",
            "",
            "def make_auth_headers(role: str) -> dict:",
            '    """Create Authorization headers with a valid JWT for the given role."""',
            "    payload = {",
            '        "sub": "test-user-id",',
            '        "email": "test@example.com",',
            f'        "role": role,',
            '        "exp": datetime.now(timezone.utc) + timedelta(hours=1),',
            "    }",
            '    token = jwt.encode(payload, AUTH_SECRET, algorithm="HS256")',
            '    return {"Authorization": f"Bearer {token}"}',
            "",
            "",
            "@pytest.fixture",
            "def admin_headers():",
            f'    """Auth headers for the {roles[0]!r} role."""',
            f'    return make_auth_headers("{roles[0]}")',
            "",
        ])

    return GeneratedFile(
        path="backend/tests/conftest.py",
        content="\n".join(lines),
        provenance=f"domain/{ir.domain}",
    )


# =============================================================================
# Per-entity test file generation
# =============================================================================


def _generate_entity_tests(
    route: RouteIR,
    entity: EntityIR,
    auth_infra: Optional[InfraIR],
) -> GeneratedFile:
    """Generate backend/tests/test_{entity}.py."""
    entity_name = entity.name
    base_path = route.base_path or f"/{entity_name}s"
    header = provenance_header("python", route.fqn, f"Tests for {entity_name} API")
    has_pipeline = bool(entity.ai_hooks)
    pipeline_marker = "@pytest.mark.requires_pipeline\n" if has_pipeline else ""

    # Determine auth details
    auth_role: str | None = None
    unauth_role: str | None = None
    if auth_infra:
        roles = auth_infra.config.get("roles", [])
        protected = auth_infra.config.get("protected_routes", [])
        # Find if this entity's base_path is protected
        for pr in protected:
            if pr.get("path", "").rstrip("/") == base_path.rstrip("/"):
                allowed = pr.get("roles", roles[:1])
                auth_role = allowed[0] if allowed else (roles[0] if roles else "admin")
                # Pick a role NOT in the allowed list for 403 tests
                unauth_role = next((r for r in roles if r not in allowed), None)
                break

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
    ]
    if auth_role:
        lines.append("from backend.tests.conftest import make_auth_headers")
        lines.append("")

    valid_payload = _valid_payload_code(entity)

    # Helper: create a record and return its id
    lines.append("")
    lines.append(f"VALID_PAYLOAD = {valid_payload}")
    lines.append("")
    lines.append("")
    lines.append(f"def _create_{entity_name}(client):")
    lines.append(f'    """Helper — POST a valid {entity_name} and return the response."""')
    if auth_role:
        lines.append(f'    headers = make_auth_headers("{auth_role}")')
        lines.append(f'    resp = client.post("{base_path}/", json=VALID_PAYLOAD, headers=headers)')
    else:
        lines.append(f'    resp = client.post("{base_path}/", json=VALID_PAYLOAD)')
    lines.append("    assert resp.status_code == 201")
    lines.append("    return resp.json()")
    lines.append("")

    # Generate tests for each endpoint
    for ep in route.endpoints:
        lines.append("")
        lines.extend(
            _generate_endpoint_tests(
                ep, entity_name, base_path, entity, auth_role, unauth_role, pipeline_marker,
            )
        )

    return GeneratedFile(
        path=f"backend/tests/test_{entity_name}.py",
        content="\n".join(lines),
        provenance=route.fqn,
    )


def _generate_endpoint_tests(
    ep: EndpointIR,
    entity_name: str,
    base_path: str,
    entity: EntityIR,
    auth_role: str | None,
    unauth_role: str | None,
    pipeline_marker: str,
) -> list[str]:
    """Emit test functions for a single endpoint."""
    method = ep.method.lower()
    path = ep.path
    lines: list[str] = []
    headers_arg = f', headers=make_auth_headers("{auth_role}")' if auth_role else ""
    headers_kwarg = f"headers=make_auth_headers(\"{auth_role}\")" if auth_role else ""

    # --- POST / (create) ---
    if method == "post" and path == "/":
        lines.extend([
            f"{pipeline_marker}def test_create_{entity_name}(client):",
            f'    """POST {base_path}/ with valid data returns 201."""',
            f"    data = _create_{entity_name}(client)",
            f'    assert "id" in data',
            "",
            "",
            f"{pipeline_marker}def test_create_{entity_name}_missing_fields(client):",
            f'    """POST {base_path}/ with empty body returns 422."""',
        ])
        if auth_role:
            lines.append(f'    resp = client.post("{base_path}/", json={{}}, headers=make_auth_headers("{auth_role}"))')
        else:
            lines.append(f'    resp = client.post("{base_path}/", json={{}})')
        lines.extend([
            "    assert resp.status_code == 422",
            "",
        ])
        lines.extend(_auth_tests("post", base_path + "/", entity_name, "create", auth_role, unauth_role, pipeline_marker))
        return lines

    # --- GET / (list) ---
    if method == "get" and path == "/":
        lines.extend([
            f"{pipeline_marker}def test_list_{entity_name}s(client):",
            f'    """GET {base_path}/ returns items and total."""',
            f"    _create_{entity_name}(client)",
            f'    resp = client.get("{base_path}/"{headers_arg})',
            "    assert resp.status_code == 200",
            "    body = resp.json()",
            '    assert "items" in body',
            '    assert "total" in body',
            "    assert body[\"total\"] >= 1",
            "",
        ])
        lines.extend(_auth_tests("get", base_path + "/", entity_name, "list", auth_role, unauth_role, pipeline_marker))
        return lines

    # --- GET /{id} (detail) ---
    if method == "get" and "{id}" in path:
        lines.extend([
            f"{pipeline_marker}def test_get_{entity_name}(client):",
            f'    """GET {base_path}/{{id}} returns the record."""',
            f"    created = _create_{entity_name}(client)",
            f'    resp = client.get(f"{base_path}/{{created[\'id\']}}"{headers_arg})',
            "    assert resp.status_code == 200",
            "",
            "",
            f"{pipeline_marker}def test_get_{entity_name}_not_found(client):",
            f'    """GET {base_path}/{{id}} with bad id returns 404."""',
            f'    resp = client.get("{base_path}/nonexistent-id"{headers_arg})',
            "    assert resp.status_code == 404",
            "",
        ])
        lines.extend(_auth_tests("get", base_path + "/{id}", entity_name, "get", auth_role, unauth_role, pipeline_marker))
        return lines

    # --- PATCH /{id} (update) ---
    if method == "patch" and "{id}" in path:
        # Pick a field to update
        updatable = [
            f for f in entity.fields
            if not f.computed and not f.immutable and f.name != "id"
        ]
        if updatable:
            uf = updatable[0]
            update_payload = f'{{"{uf.name}": {_default_value(uf)}}}'
        else:
            update_payload = "{}"

        lines.extend([
            f"{pipeline_marker}def test_update_{entity_name}(client):",
            f'    """PATCH {base_path}/{{id}} updates the record."""',
            f"    created = _create_{entity_name}(client)",
        ])
        if auth_role:
            lines.append(f'    resp = client.patch(f"{base_path}/{{created[\'id\']}}", json={update_payload}, headers=make_auth_headers("{auth_role}"))')
        else:
            lines.append(f'    resp = client.patch(f"{base_path}/{{created[\'id\']}}", json={update_payload})')
        lines.extend([
            "    assert resp.status_code == 200",
            "",
            "",
            f"{pipeline_marker}def test_update_{entity_name}_not_found(client):",
            f'    """PATCH {base_path}/{{id}} with bad id returns 404."""',
        ])
        if auth_role:
            lines.append(f'    resp = client.patch("{base_path}/nonexistent-id", json={update_payload}, headers=make_auth_headers("{auth_role}"))')
        else:
            lines.append(f'    resp = client.patch("{base_path}/nonexistent-id", json={update_payload})')
        lines.extend([
            "    assert resp.status_code == 404",
            "",
        ])
        lines.extend(_auth_tests("patch", base_path + "/{id}", entity_name, "update", auth_role, unauth_role, pipeline_marker))
        return lines

    # --- DELETE /{id} ---
    if method == "delete" and "{id}" in path:
        lines.extend([
            f"{pipeline_marker}def test_delete_{entity_name}(client):",
            f'    """DELETE {base_path}/{{id}} removes the record."""',
            f"    created = _create_{entity_name}(client)",
        ])
        if auth_role:
            lines.append(f'    resp = client.delete(f"{base_path}/{{created[\'id\']}}", headers=make_auth_headers("{auth_role}"))')
        else:
            lines.append(f'    resp = client.delete(f"{base_path}/{{created[\'id\']}}")')
        lines.extend([
            "    assert resp.status_code == 204",
            "",
            "",
            f"{pipeline_marker}def test_delete_{entity_name}_not_found(client):",
            f'    """DELETE {base_path}/{{id}} with bad id returns 404."""',
        ])
        if auth_role:
            lines.append(f'    resp = client.delete("{base_path}/nonexistent-id", headers=make_auth_headers("{auth_role}"))')
        else:
            lines.append(f'    resp = client.delete("{base_path}/nonexistent-id")')
        lines.extend([
            "    assert resp.status_code == 404",
            "",
        ])
        lines.extend(_auth_tests("delete", base_path + "/{id}", entity_name, "delete", auth_role, unauth_role, pipeline_marker))
        return lines

    # --- PUT /{id}/state (transition) ---
    if method == "put" and "state" in path:
        lines.extend(_generate_state_tests(entity, entity_name, base_path, auth_role, pipeline_marker))
        return lines

    return lines


# =============================================================================
# Auth test helpers
# =============================================================================


def _auth_tests(
    method: str,
    path_template: str,
    entity_name: str,
    action: str,
    auth_role: str | None,
    unauth_role: str | None,
    pipeline_marker: str,
) -> list[str]:
    """Generate 401/403 tests for a protected endpoint."""
    if not auth_role:
        return []

    lines: list[str] = []
    # Use a concrete path for endpoints with {id}
    needs_record = "{id}" in path_template
    path_expr = f"{path_template}" if not needs_record else path_template.replace("{id}", "nonexistent-id")

    json_arg = ", json={}" if method in ("post", "patch", "put") else ""

    lines.extend([
        "",
        f"{pipeline_marker}def test_{action}_{entity_name}_unauthenticated(client):",
        f'    """Unauthenticated {method.upper()} returns 401."""',
        f'    resp = client.{method}("{path_expr}"{json_arg})',
        "    assert resp.status_code == 401",
        "",
    ])

    if unauth_role:
        lines.extend([
            "",
            f"{pipeline_marker}def test_{action}_{entity_name}_wrong_role(client):",
            f'    """{method.upper()} with wrong role returns 403."""',
            f'    resp = client.{method}("{path_expr}"{json_arg}, headers=make_auth_headers("{unauth_role}"))',
            "    assert resp.status_code == 403",
            "",
        ])

    return lines


# =============================================================================
# State machine test generation
# =============================================================================


def _generate_state_tests(
    entity: EntityIR,
    entity_name: str,
    base_path: str,
    auth_role: str | None,
    pipeline_marker: str,
) -> list[str]:
    """Generate state transition tests."""
    sm = entity.state_machine
    if not sm:
        return []

    lines: list[str] = []
    headers_arg = f', headers=make_auth_headers("{auth_role}")' if auth_role else ""

    # Happy path: initial -> first valid target
    initial = sm.initial
    targets = sm.transitions.get(initial, [])
    if targets:
        target = targets[0]
        lines.extend([
            f"{pipeline_marker}def test_transition_{entity_name}(client):",
            f'    """PUT {base_path}/{{id}}/state transitions from {initial} to {target}."""',
            f"    created = _create_{entity_name}(client)",
            f'    resp = client.put(',
            f'        f"{base_path}/{{created[\'id\']}}/state",',
            f'        json={{"state": "{target}"}}{headers_arg},',
            "    )",
            "    assert resp.status_code == 200",
            "",
            "",
        ])

    # Invalid transition: find a state NOT in the valid targets from initial
    all_states = [s.name for s in sm.states]
    invalid_targets = [s for s in all_states if s != initial and s not in targets]
    if invalid_targets:
        bad = invalid_targets[0]
        lines.extend([
            f"{pipeline_marker}def test_transition_{entity_name}_invalid(client):",
            f'    """PUT {base_path}/{{id}}/state with invalid transition returns 422."""',
            f"    created = _create_{entity_name}(client)",
            f'    resp = client.put(',
            f'        f"{base_path}/{{created[\'id\']}}/state",',
            f'        json={{"state": "{bad}"}}{headers_arg},',
            "    )",
            "    assert resp.status_code == 422",
            "",
            "",
        ])

    # Missing state field
    lines.extend([
        f"{pipeline_marker}def test_transition_{entity_name}_missing_state(client):",
        f'    """PUT {base_path}/{{id}}/state with empty body returns 422."""',
        f"    created = _create_{entity_name}(client)",
        f'    resp = client.put(',
        f'        f"{base_path}/{{created[\'id\']}}/state",',
        f"        json={{}}{headers_arg},",
        "    )",
        "    assert resp.status_code == 422",
        "",
        "",
    ])

    # Guard violation tests — marked xfail until gen_routes.py enforces guards
    for guard in sm.guards:
        if guard.require_fields:
            fields_str = ", ".join(guard.require_fields)
            lines.extend([
                f'@pytest.mark.xfail(reason="guard enforcement not yet generated in route handlers")',
                f"{pipeline_marker}def test_transition_{entity_name}_{guard.from_state}_to_{guard.to_state}_guard(client):",
                f'    """Transition {guard.from_state} -> {guard.to_state} requires {fields_str}."""',
                f"    created = _create_{entity_name}(client)",
            ])
            # Ensure entity is in the right source state if it's not initial
            if guard.from_state != initial:
                # We need to transition to from_state first — find a path
                path_to_source = _find_transition_path(sm, initial, guard.from_state)
                if path_to_source:
                    for step in path_to_source:
                        lines.extend([
                            f'    client.put(',
                            f'        f"{base_path}/{{created[\'id\']}}/state",',
                            f'        json={{"state": "{step}"}}{headers_arg},',
                            "    )",
                        ])

            lines.extend([
                f'    resp = client.put(',
                f'        f"{base_path}/{{created[\'id\']}}/state",',
                f'        json={{"state": "{guard.to_state}"}}{headers_arg},',
                "    )",
                "    assert resp.status_code == 422",
                "",
                "",
            ])

    return lines


def _find_transition_path(
    sm: StateMachineIR, start: str, end: str,
) -> list[str] | None:
    """BFS for a path from start to end in the transition graph. Returns intermediate states."""
    if start == end:
        return []
    visited: set[str] = {start}
    queue: list[tuple[str, list[str]]] = [(start, [])]
    while queue:
        current, path = queue.pop(0)
        for target in sm.transitions.get(current, []):
            if target == end:
                return path + [target]
            if target not in visited:
                visited.add(target)
                queue.append((target, path + [target]))
    return None


# =============================================================================
# Orchestrator
# =============================================================================


def generate_tests(ir: DomainIR) -> list[GeneratedFile]:
    """Generate pytest test files for each entity's API routes."""
    if not ir.routes:
        return []

    entity_map = {e.fqn: e for e in ir.entities}
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    files: list[GeneratedFile] = []

    files.append(_generate_conftest(ir))

    for route in ir.routes:
        entity = entity_map.get(route.entity_fqn)
        if not entity:
            continue
        files.append(_generate_entity_tests(route, entity, auth_infra))

    return files

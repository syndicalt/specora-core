"""Generate FastAPI route handlers that call repositories."""
from __future__ import annotations

from forge.ir.model import DomainIR, EndpointIR, EntityIR, RouteIR
from forge.targets.base import GeneratedFile, provenance_header

PYTHON_TYPE_MAP = {
    "string": "str", "integer": "int", "number": "float", "boolean": "bool",
    "text": "str", "array": "list", "object": "dict", "datetime": "str",
    "date": "str", "uuid": "str", "email": "str",
}


def _to_class(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def generate_routes(ir: DomainIR) -> list[GeneratedFile]:
    """Generate one route module per Route contract."""
    entity_map = {e.fqn: e for e in ir.entities}
    # Check for auth
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    files = []

    for route in ir.routes:
        entity = entity_map.get(route.entity_fqn)
        files.append(_generate_route(route, entity, auth_infra))

    return files


def _generate_route(route: RouteIR, entity: EntityIR | None, auth_infra) -> GeneratedFile:
    header = provenance_header("python", route.fqn, f"API routes for {route.name}")

    entity_name = route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name
    cls = _to_class(entity_name)
    base_path = route.base_path or f"/{entity_name}s"

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import uuid",
        "from datetime import datetime, timezone",
        "from typing import Any",
        "",
        "from fastapi import APIRouter, Depends, HTTPException",
        "",
        f"from backend.models import {cls}Create, {cls}Update, {cls}Response",
        f"from backend.repositories.base import {cls}Repository, get_{entity_name}_repo",
    ]

    if auth_infra:
        lines.append("from backend.auth.middleware import require_auth, require_role")

    lines.extend([
        "",
        f'router = APIRouter(prefix="{base_path}", tags=["{entity_name}"])',
        "",
        "",
    ])

    for endpoint in route.endpoints:
        lines.extend(_generate_endpoint(endpoint, entity_name, cls, base_path, entity, auth_infra))
        lines.append("")

    return GeneratedFile(
        path=f"backend/routes_{entity_name}.py",
        content="\n".join(lines),
        provenance=route.fqn,
    )


def _generate_endpoint(endpoint, entity_name, cls, base_path, entity, auth_infra) -> list[str]:
    lines = []
    method = endpoint.method.lower()
    path = endpoint.path
    repo_dep = f"repo: {cls}Repository = Depends(get_{entity_name}_repo)"
    auth_dep = ""
    if auth_infra:
        auth_dep = ", user = Depends(require_auth)"

    if method == "get" and path == "/":
        lines.append(f'@router.get("/")')
        lines.append(f"async def list_{entity_name}s(limit: int = 100, offset: int = 0, {repo_dep}{auth_dep}):")
        lines.append(f'    """List {entity_name}s."""')
        lines.append(f"    items, total = await repo.list(limit=limit, offset=offset)")
        lines.append(f"    return {{'items': items, 'total': total}}")
        return lines

    if method == "get" and "{id}" in path:
        lines.append(f'@router.get("/{{record_id}}")')
        lines.append(f"async def get_{entity_name}(record_id: str, {repo_dep}{auth_dep}):")
        lines.append(f'    """Get {entity_name} by ID."""')
        lines.append(f"    record = await repo.get(record_id)")
        lines.append(f"    if not record:")
        lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
        lines.append(f"    return record")
        return lines

    if method == "post" and path == "/":
        status = endpoint.response_status or 201
        lines.append(f'@router.post("/", status_code={status})')
        lines.append(f"async def create_{entity_name}(body: {cls}Create, {repo_dep}{auth_dep}):")
        lines.append(f'    """Create {entity_name}."""')
        lines.append(f"    data = body.model_dump(exclude_none=True)")
        for field_name, expr in endpoint.auto_fields.items():
            if "uuid" in expr.lower():
                lines.append(f'    data["{field_name}"] = str(uuid.uuid4())')
            elif "now" in expr.lower():
                lines.append(f'    data["{field_name}"] = datetime.now(timezone.utc).isoformat()')
        lines.append(f"    record = await repo.create(data)")
        lines.append(f'    record["_links"] = {{"self": f"{base_path}/{{record[\'id\']}}"}}')
        lines.append(f"    return record")
        return lines

    if method == "patch" and "{id}" in path:
        lines.append(f'@router.patch("/{{record_id}}")')
        lines.append(f"async def update_{entity_name}(record_id: str, body: {cls}Update, {repo_dep}{auth_dep}):")
        lines.append(f'    """Update {entity_name}."""')
        lines.append(f"    data = body.model_dump(exclude_none=True)")
        lines.append(f"    record = await repo.update(record_id, data)")
        lines.append(f"    if not record:")
        lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
        lines.append(f"    return record")
        return lines

    if method == "delete" and "{id}" in path:
        lines.append(f'@router.delete("/{{record_id}}", status_code=204)')
        lines.append(f"async def delete_{entity_name}(record_id: str, {repo_dep}{auth_dep}):")
        lines.append(f'    """Delete {entity_name}."""')
        lines.append(f"    deleted = await repo.delete(record_id)")
        lines.append(f"    if not deleted:")
        lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
        lines.append(f"    return None")
        return lines

    if method == "put" and "state" in path:
        lines.append(f'@router.put("/{{record_id}}/state")')
        lines.append(f"async def transition_{entity_name}(record_id: str, body: dict[str, Any], {repo_dep}{auth_dep}):")
        lines.append(f'    """Transition {entity_name} state."""')
        lines.append(f'    new_state = body.get("state")')
        lines.append(f"    if not new_state:")
        lines.append(f'        raise HTTPException(422, detail={{"error": "state required"}})')
        lines.append(f"    record = await repo.transition(record_id, new_state)")
        lines.append(f"    if not record:")
        lines.append(f'        raise HTTPException(422, detail={{"error": "invalid_transition"}})')
        lines.append(f"    return record")
        return lines

    # Fallback
    lines.append(f'@router.{method}("{path}")')
    lines.append(f"async def {method}_{entity_name}_{path.replace('/', '_').strip('_')}():")
    lines.append(f'    """{endpoint.summary}"""')
    lines.append(f'    return {{"message": "not implemented"}}')
    return lines

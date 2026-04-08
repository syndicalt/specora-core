"""FastAPI route generator — RouteIR + EntityIR -> FastAPI route handlers.

Generates a Python module with FastAPI APIRouter containing route
handlers for each endpoint defined in Route contracts. Each handler
includes request validation, auto-field computation, and HATEOAS links.

Generated code uses a generic data store abstraction (not a real
database) so the generated routes are functional for testing but
the actual persistence layer is pluggable.

Usage:
    from forge.targets.fastapi.gen_routes import FastAPIGenerator

    gen = FastAPIGenerator()
    files = gen.generate(ir)
"""

from __future__ import annotations

from forge.ir.model import DomainIR, EndpointIR, EntityIR, RouteIR
from forge.targets.base import BaseGenerator, GeneratedFile, provenance_header

# IR type -> Python type hint
PYTHON_TYPE_MAP: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "text": "str",
    "array": "list",
    "object": "dict",
    "datetime": "str",
    "date": "str",
    "uuid": "str",
    "email": "str",
}


class FastAPIGenerator(BaseGenerator):
    """Generates FastAPI route modules from Route and Entity definitions."""

    def name(self) -> str:
        return "fastapi"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        """Generate FastAPI route files.

        Produces one routes.py file per Route contract, plus a shared
        models.py with Pydantic request/response models.

        Args:
            ir: The compiled DomainIR.

        Returns:
            List of GeneratedFile objects.
        """
        files: list[GeneratedFile] = []

        # Build entity lookup for cross-referencing
        entity_map = {e.fqn: e for e in ir.entities}

        # Generate models file
        if ir.entities:
            files.append(self._generate_models(ir))

        # Generate route files
        for route in ir.routes:
            entity = entity_map.get(route.entity_fqn)
            files.append(self._generate_route_module(route, entity))

        # Generate app.py that wires everything together
        if ir.routes:
            files.append(self._generate_app(ir))

        return files

    def _generate_models(self, ir: DomainIR) -> GeneratedFile:
        """Generate Pydantic models for all entities."""
        provenance_fqns = ", ".join(e.fqn for e in ir.entities)
        header = provenance_header("python", provenance_fqns, "Pydantic models for request/response validation")

        lines = [
            header,
            "from __future__ import annotations",
            "",
            "from typing import Any, Optional",
            "",
            "from pydantic import BaseModel, Field",
            "",
            "",
        ]

        for entity in ir.entities:
            class_name = self._to_class_name(entity.name)

            # Create model
            lines.append(f'class {class_name}Create(BaseModel):')
            lines.append(f'    """Create request for {entity.name}."""')
            create_fields = [f for f in entity.fields if not f.computed and not f.immutable]
            if create_fields:
                for field in create_fields:
                    py_type = PYTHON_TYPE_MAP.get(field.type, "Any")
                    if not field.required:
                        py_type = f"Optional[{py_type}]"
                    default = " = None" if not field.required else ""
                    lines.append(f"    {field.name}: {py_type}{default}")
            else:
                lines.append("    pass")
            lines.append("")
            lines.append("")

            # Response model
            lines.append(f'class {class_name}Response(BaseModel):')
            lines.append(f'    """Response model for {entity.name}."""')
            for field in entity.fields:
                py_type = PYTHON_TYPE_MAP.get(field.type, "Any")
                if not field.required:
                    py_type = f"Optional[{py_type}]"
                default = " = None" if not field.required else ""
                lines.append(f"    {field.name}: {py_type}{default}")
            lines.append("    links: dict[str, str] = Field(default_factory=dict, alias='_links')")
            lines.append("")
            lines.append("")

        return GeneratedFile(
            path="backend/models.py",
            content="\n".join(lines),
            provenance=provenance_fqns,
        )

    def _generate_route_module(self, route: RouteIR, entity: EntityIR | None) -> GeneratedFile:
        """Generate a FastAPI router module for a Route contract."""
        header = provenance_header("python", route.fqn, f"API routes for {route.name}")

        entity_name = route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name
        class_name = self._to_class_name(entity_name)
        base_path = route.base_path or f"/{entity_name}s"

        lines = [
            header,
            "from __future__ import annotations",
            "",
            "import uuid",
            "from datetime import datetime, timezone",
            "from typing import Any",
            "",
            "from fastapi import APIRouter, HTTPException",
            "",
            f"from backend.models import {class_name}Create, {class_name}Response",
            "",
            f'router = APIRouter(prefix="{base_path}", tags=["{entity_name}"])',
            "",
            f"# In-memory store (replace with real database in production)",
            f"_store: dict[str, dict] = {{}}",
            "",
            "",
        ]

        for endpoint in route.endpoints:
            lines.extend(self._generate_endpoint(endpoint, entity_name, class_name, base_path))
            lines.append("")

        return GeneratedFile(
            path=f"backend/routes_{entity_name}.py",
            content="\n".join(lines),
            provenance=route.fqn,
        )

    def _generate_endpoint(
        self,
        endpoint: EndpointIR,
        entity_name: str,
        class_name: str,
        base_path: str,
    ) -> list[str]:
        """Generate a single FastAPI endpoint handler."""
        lines: list[str] = []
        method = endpoint.method.lower()
        path = endpoint.path

        if method == "get" and path == "/":
            # List endpoint
            lines.append(f'@router.get("/")')
            lines.append(f"async def list_{entity_name}s(limit: int = 100, offset: int = 0):")
            lines.append(f'    """List {entity_name}s."""')
            lines.append(f"    items = list(_store.values())[offset:offset + limit]")
            lines.append(f"    return {{'items': items, 'total': len(_store)}}")
            return lines

        if method == "get" and "{id}" in path:
            # Get by ID
            lines.append(f'@router.get("/{{record_id}}")')
            lines.append(f"async def get_{entity_name}(record_id: str):")
            lines.append(f'    """Get a {entity_name} by ID."""')
            lines.append(f"    record = _store.get(record_id)")
            lines.append(f"    if not record:")
            lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
            lines.append(f"    return record")
            return lines

        if method == "post" and path == "/":
            # Create
            lines.append(f'@router.post("/", status_code={endpoint.response_status or 201})')
            lines.append(f"async def create_{entity_name}(body: {class_name}Create):")
            lines.append(f'    """Create a new {entity_name}."""')
            lines.append(f"    record = body.model_dump(exclude_none=True)")
            # Auto-fields
            for field_name, expr in endpoint.auto_fields.items():
                if "uuid" in expr.lower():
                    lines.append(f'    record["{field_name}"] = str(uuid.uuid4())')
                elif "now" in expr.lower():
                    lines.append(f'    record["{field_name}"] = datetime.now(timezone.utc).isoformat()')
                else:
                    lines.append(f'    record["{field_name}"] = "{expr}"  # auto-computed')
            lines.append(f'    record["links"] = {{"self": f"{base_path}/{{record.get(\'id\', \'\')}}"}}')
            lines.append(f'    _store[record.get("id", "")] = record')
            lines.append(f"    return record")
            return lines

        if method in ("put", "patch") and "{id}" in path:
            # Update
            lines.append(f'@router.{method}("/{{record_id}}")')
            lines.append(f"async def update_{entity_name}(record_id: str, body: dict[str, Any]):")
            lines.append(f'    """Update a {entity_name}."""')
            lines.append(f"    record = _store.get(record_id)")
            lines.append(f"    if not record:")
            lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
            lines.append(f"    record.update(body)")
            lines.append(f'    record["updated_at"] = datetime.now(timezone.utc).isoformat()')
            lines.append(f"    return record")
            return lines

        if method == "delete" and "{id}" in path:
            # Delete
            lines.append(f'@router.delete("/{{record_id}}", status_code=204)')
            lines.append(f"async def delete_{entity_name}(record_id: str):")
            lines.append(f'    """Delete a {entity_name}."""')
            lines.append(f"    if record_id not in _store:")
            lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
            lines.append(f"    del _store[record_id]")
            lines.append(f"    return None")
            return lines

        # Generic fallback for custom endpoints
        lines.append(f'@router.{method}("{path}")')
        lines.append(f"async def {method}_{entity_name}_{path.replace('/', '_').strip('_')}():")
        lines.append(f'    """{endpoint.summary}"""')
        lines.append(f'    return {{"message": "not implemented"}}')
        return lines

    def _generate_app(self, ir: DomainIR) -> GeneratedFile:
        """Generate the FastAPI app that wires all routers together."""
        header = provenance_header("python", f"domain/{ir.domain}", "FastAPI application entrypoint")

        route_imports: list[str] = []
        route_includes: list[str] = []
        for route in ir.routes:
            entity_name = route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name
            module = f"routes_{entity_name}"
            route_imports.append(f"from backend.{module} import router as {entity_name}_router")
            route_includes.append(f"app.include_router({entity_name}_router)")

        lines = [
            header,
            "from fastapi import FastAPI",
            "",
            *route_imports,
            "",
            'app = FastAPI(title="Specora Generated API")',
            "",
            *route_includes,
            "",
            "",
            '@app.get("/health")',
            "async def health():",
            '    return {"status": "ok"}',
            "",
        ]

        return GeneratedFile(
            path="backend/app.py",
            content="\n".join(lines),
            provenance=f"domain/{ir.domain}",
        )

    def _to_class_name(self, name: str) -> str:
        """Convert snake_case to PascalCase."""
        return "".join(part.capitalize() for part in name.split("_"))

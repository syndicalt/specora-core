"""Generate Pydantic models for request/response validation."""
from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, FieldIR
from forge.targets.base import GeneratedFile, provenance_header

PYTHON_TYPE_MAP = {
    "string": "str", "integer": "int", "number": "float", "boolean": "bool",
    "text": "str", "array": "list", "object": "dict", "datetime": "str",
    "date": "str", "uuid": "str", "email": "str",
}


def _to_class(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def generate_models(ir: DomainIR) -> GeneratedFile:
    """Generate backend/models.py with Pydantic models."""
    if not ir.entities:
        return GeneratedFile(path="backend/models.py", content="", provenance="")

    fqns = ", ".join(e.fqn for e in ir.entities)
    header = provenance_header("python", fqns, "Pydantic models for request/response validation")

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
        cls = _to_class(entity.name)

        # Create model — exclude computed and immutable fields
        lines.append(f"class {cls}Create(BaseModel):")
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

        # Update model — all fields optional
        lines.append(f"class {cls}Update(BaseModel):")
        lines.append(f'    """Update request for {entity.name}."""')
        update_fields = [f for f in entity.fields if not f.computed and not f.immutable and f.name != "id"]
        if update_fields:
            for field in update_fields:
                py_type = PYTHON_TYPE_MAP.get(field.type, "Any")
                lines.append(f"    {field.name}: Optional[{py_type}] = None")
        else:
            lines.append("    pass")
        lines.append("")
        lines.append("")

        # Response model — all fields
        lines.append(f"class {cls}Response(BaseModel):")
        lines.append(f'    """Response model for {entity.name}."""')
        for field in entity.fields:
            py_type = PYTHON_TYPE_MAP.get(field.type, "Any")
            if not field.required:
                py_type = f"Optional[{py_type}]"
            default = " = None" if not field.required else ""
            lines.append(f"    {field.name}: {py_type}{default}")
        lines.append("    links: dict[str, str] = Field(default_factory=dict, alias='_links')")
        lines.append("")
        lines.append("    model_config = {'populate_by_name': True}")
        lines.append("")
        lines.append("")

    return GeneratedFile(
        path="backend/models.py",
        content="\n".join(lines),
        provenance=fqns,
    )

"""Generate Pydantic models for request/response validation.

Four models per entity, and the split between them is where the API's write
authority is decided:

    <Cls>Create       what a client may supply on POST
    <Cls>Update       what a client may supply on PATCH
    <Cls>Response     what the server is willing to disclose
    <Cls>Page         a keyset page of <Cls>Response

Plus <Cls>StateChange for entities that bind a workflow.
"""
from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, FieldIR
from forge.targets.base import GeneratedFile, provenance_header
from forge.targets.naming import class_name
from forge.targets.typemap import py_type, required_imports

# The lifecycle field that `bind_state_machines` projects onto any entity with
# a bound workflow. It is excluded from every request model. The field is
# neither `computed` nor `immutable` in the IR, so the ordinary filters let it
# through — and a state machine whose state can be assigned directly by a
# create or update body is not a state machine at all: every transition rule
# and guard in the contract becomes advisory, and a record can be created
# straight into a terminal state. It moves only through the transition
# endpoint, which is the single path that consults the machine.
STATE_FIELD = "state"

# Request models reject unknown keys rather than dropping them. Silently
# ignoring `{"state": "churned"}` would leave a caller believing the write
# landed; a 422 says which key was refused.
STRICT_REQUEST_CONFIG = '    model_config = {"extra": "forbid"}'


def _lifecycle_managed(entity: EntityIR, field: FieldIR) -> bool:
    """Whether this field is the workflow's state and therefore server-owned.

    Matching on the name alone would also catch an entity that legitimately
    has a `state` field (a postal address, say), so the entity must actually
    bind a state machine for the field to be treated as lifecycle-managed.
    """
    return entity.state_machine is not None and field.name == STATE_FIELD


def _writable_fields(entity: EntityIR) -> list[FieldIR]:
    """Fields a client is permitted to set.

    `sensitive` fields stay here: write-only means write-*able*. A password
    hash that could not be set would leave the account with no credential.
    """
    return [
        f
        for f in entity.fields
        if not f.computed and not f.immutable and not _lifecycle_managed(entity, f)
    ]


def _disclosable_fields(entity: EntityIR) -> list[FieldIR]:
    """Fields the server is willing to serialise back to a client.

    `sensitive` fields are omitted from the response model outright rather than
    defaulted to None or marked `exclude=True`. An excluded-but-declared field
    is one `response_model_exclude` override, one `.model_dump()` in a future
    handler, or one `by_alias` flag away from being emitted again; a field the
    class does not have cannot be serialised by any of them.
    """
    return [f for f in entity.fields if not f.sensitive]


def _annotation(field: FieldIR, *, optional: bool) -> str:
    py = py_type(field.type)
    return f"Optional[{py}]" if optional else py


def _import_block(ir_types: set[str]) -> list[str]:
    """Build the module's imports from the types actually present.

    A fixed preamble emits names the module does not use, which the generated
    app's own lint gate rejects.
    """
    lines = ["from __future__ import annotations", ""]

    # `Optional` is unconditional: every entity gets a <Cls>Page whose
    # `next_cursor` is optional.
    typing_names = {"Optional"}
    if any(py_type(t) == "Any" for t in ir_types):
        typing_names.add("Any")

    pydantic_names = {"BaseModel", "Field"}
    stdlib_imports: list[str] = []
    for imp in required_imports(ir_types):
        if imp.startswith("from pydantic import "):
            names = imp.removeprefix("from pydantic import ")
            pydantic_names.update(n.strip() for n in names.split(","))
        else:
            stdlib_imports.append(imp)

    stdlib_imports.append(f"from typing import {', '.join(sorted(typing_names))}")
    lines.extend(sorted(stdlib_imports))
    lines.append("")
    lines.append(f"from pydantic import {', '.join(sorted(pydantic_names))}")
    lines.extend(["", ""])
    return lines


def _create_model(entity: EntityIR, cls: str) -> list[str]:
    lines = [
        f"class {cls}Create(BaseModel):",
        f'    """Create request for {entity.name}."""',
        "",
    ]
    for field in _writable_fields(entity):
        annotation = _annotation(field, optional=not field.required)
        default = "" if field.required else " = None"
        lines.append(f"    {field.name}: {annotation}{default}")
    lines.extend(["", STRICT_REQUEST_CONFIG, "", ""])
    return lines


def _update_model(entity: EntityIR, cls: str) -> list[str]:
    lines = [
        f"class {cls}Update(BaseModel):",
        f'    """Update request for {entity.name}."""',
        "",
    ]
    # Every field is optional so a PATCH can carry a subset. The handler uses
    # `exclude_unset` rather than `exclude_none` to tell "omitted" from
    # "explicitly null" — without that distinction a nullable field could
    # never be cleared.
    for field in _writable_fields(entity):
        if field.name == "id":
            continue
        lines.append(f"    {field.name}: {_annotation(field, optional=True)} = None")
    lines.extend(["", STRICT_REQUEST_CONFIG, "", ""])
    return lines


def _response_model(entity: EntityIR, cls: str) -> list[str]:
    lines = [
        f"class {cls}Response(BaseModel):",
        f'    """Response model for {entity.name}."""',
        "",
    ]
    for field in _disclosable_fields(entity):
        annotation = _annotation(field, optional=not field.required)
        default = "" if field.required else " = None"
        lines.append(f"    {field.name}: {annotation}{default}")
    lines.append("    links: dict[str, str] = Field(default_factory=dict, alias=\"_links\")")
    lines.append("")
    # `extra: ignore` is pydantic's default, but it is what drops a sensitive
    # column that a handler passed through, so it is stated rather than relied
    # on from elsewhere.
    lines.append('    model_config = {"populate_by_name": True, "extra": "ignore"}')
    lines.extend(["", ""])
    return lines


def _page_model(entity: EntityIR, cls: str) -> list[str]:
    return [
        f"class {cls}Page(BaseModel):",
        f'    """A keyset page of {entity.name} records."""',
        "",
        f"    items: list[{cls}Response]",
        "    next_cursor: Optional[str] = None",
        "",
        "",
    ]


def _state_change_model(entity: EntityIR, cls: str) -> list[str]:
    """Body model for the transition endpoint.

    The target state is not validated against the machine's states here: the
    repository owns transition legality and reports `invalid_transition` so
    that the reason for a refusal comes from one place rather than two.
    """
    return [
        f"class {cls}StateChange(BaseModel):",
        f'    """Requested lifecycle transition for {entity.name}."""',
        "",
        "    state: str = Field(min_length=1)",
        "",
        STRICT_REQUEST_CONFIG,
        "",
        "",
    ]


def generate_models(ir: DomainIR) -> GeneratedFile:
    """Generate backend/models.py with Pydantic models."""
    if not ir.entities:
        return GeneratedFile(path="backend/models.py", content="", provenance="")

    fqns = ", ".join(e.fqn for e in ir.entities)
    header = provenance_header("python", fqns, "Pydantic models for request/response validation")

    ir_types = {f.type for e in ir.entities for f in e.fields}

    lines = [header]
    lines.extend(_import_block(ir_types))

    for entity in ir.entities:
        cls = class_name(entity.name, entity.domain, multi_domain=ir.multi_domain)
        lines.extend(_create_model(entity, cls))
        lines.extend(_update_model(entity, cls))
        lines.extend(_response_model(entity, cls))
        lines.extend(_page_model(entity, cls))
        if entity.state_machine is not None:
            lines.extend(_state_change_model(entity, cls))

    return GeneratedFile(
        path="backend/models.py",
        content="\n".join(lines),
        provenance=fqns,
    )

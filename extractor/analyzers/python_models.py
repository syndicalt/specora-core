"""Pass 2a: Extract entities from Python model files.

This is a pure `ast` reader. It never imports, executes, or evaluates anything
from the scanned tree, and it never sends the tree anywhere: the source being
analyzed is somebody's proprietary codebase, and Python's own parser answers
every structural question an LLM was previously being asked to guess at.

What it recognises:
  * Pydantic models  — `BaseModel` / `SQLModel` subclasses, annotated fields,
    `Field(...)` metadata.
  * SQLAlchemy models — declarative `Base` subclasses, `Column(...)` and
    `mapped_column(...)`, including `ForeignKey`, `nullable`, and column-type
    parameters.
  * Dataclasses, `TypedDict`, `NamedTuple` — annotated fields.
  * `Enum` classes and `Literal[...]`, resolved into contract `enum` values.

What it deliberately does not recognise is listed in `docs/extractor.md`.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from extractor.models import Confidence, ExtractedEntity, ExtractedField, is_sensitive_name

logger = logging.getLogger(__name__)

# Base classes that identify a class as a data model.
MODEL_BASES = {
    "BaseModel",
    "SQLModel",
    "Base",
    "Model",
    "DeclarativeBase",
    "TypedDict",
    "NamedTuple",
    "Schema",
    "ModelSchema",
}

MODEL_DECORATORS = {"dataclass", "define", "attrs", "attr_s", "s"}

ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}

# Python / Pydantic annotation -> contract field type.
PY_ANNOTATION_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "Decimal": "decimal",
    "condecimal": "decimal",
    "bool": "boolean",
    "bytes": "text",
    "datetime": "datetime",
    "date": "date",
    "UUID": "uuid",
    "uuid4": "uuid",
    "EmailStr": "email",
    "dict": "object",
    "Dict": "object",
    "Mapping": "object",
    "Any": "object",
    "list": "array",
    "List": "array",
    "set": "array",
    "Set": "array",
    "tuple": "array",
    "Tuple": "array",
    "Sequence": "array",
}

# SQLAlchemy column type -> contract field type.
SQL_COLUMN_TYPES = {
    "String": "string",
    "Unicode": "string",
    "VARCHAR": "string",
    "CHAR": "string",
    "Text": "text",
    "UnicodeText": "text",
    "TEXT": "text",
    "Integer": "integer",
    "BigInteger": "integer",
    "SmallInteger": "integer",
    "INTEGER": "integer",
    "Float": "number",
    "REAL": "number",
    "Double": "number",
    "Numeric": "decimal",
    "DECIMAL": "decimal",
    "NUMERIC": "decimal",
    "Money": "decimal",
    "Boolean": "boolean",
    "BOOLEAN": "boolean",
    "DateTime": "datetime",
    "TIMESTAMP": "datetime",
    "Date": "date",
    "Time": "string",
    "UUID": "uuid",
    "GUID": "uuid",
    "Uuid": "uuid",
    "JSON": "object",
    "JSONB": "object",
    "ARRAY": "array",
    "LargeBinary": "text",
}

# Suffix/prefix pairs that make a class a projection of another model rather
# than a model in its own right. `TicketCreate`, `TicketUpdate`, and
# `TicketResponse` are three views of one `ticket` entity; emitting three
# entities from them is the single largest source of false positives.
DTO_SUFFIXES = (
    "CreateRequest",
    "UpdateRequest",
    "Create",
    "Update",
    "Response",
    "Request",
    "Payload",
    "Patch",
    "InDB",
    "InDb",
    "Read",
    "Write",
    "Schema",
    "Model",
    "Entity",
    "Record",
    "Row",
    "Table",
    "DTO",
    "Dto",
    "Base",
    "Out",
    "In",
)

DTO_PREFIXES = ("Create", "Update", "New", "Partial")

# Classes that describe a page of results, not a domain object.
ENVELOPE_FIELDS = {"items", "results", "data", "records", "edges"}

# Classes that are UI, transport, or configuration plumbing rather than domain
# objects. Matched on the class name because their field shapes are
# indistinguishable from a real model's.
NON_ENTITY_SUFFIXES = (
    "Props",
    "Params",
    "Options",
    "Config",
    "Settings",
    "Context",
    "State",
    "Result",
    "Spec",
    "Error",
)


def analyze_python_models(
    file_paths: list[str],
    root: Path,
    *,
    warnings: list[str] | None = None,
) -> list[ExtractedEntity]:
    """Extract entities from Python model files.

    Args:
        file_paths: Paths relative to `root`, as produced by the scanner.
        root: The scan root.
        warnings: Optional sink for files that could not be analyzed. A file
            that fails to parse is reported, never silently dropped — the whole
            promise of the output is that it describes the input.
    """
    notes = warnings if warnings is not None else []
    entities: list[ExtractedEntity] = []

    for rel in file_paths:
        source = _read(root / rel, rel, notes)
        if source is None:
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except (SyntaxError, ValueError, RecursionError) as e:
            notes.append(f"{rel}: skipped, not parseable as Python ({e})")
            continue
        entities.extend(_entities_from_module(tree, rel, notes))

    return entities


def _read(path: Path, rel: str, notes: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        notes.append(f"{rel}: skipped, cannot read ({e.strerror or e})")
        return None


def _entities_from_module(tree: ast.Module, rel: str, notes: list[str]) -> list[ExtractedEntity]:
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    enums = {c.name: _enum_values(c) for c in classes if _is_enum(c)}

    entities: list[ExtractedEntity] = []
    for cls in classes:
        if cls.name in enums:
            continue
        if cls.name.endswith(NON_ENTITY_SUFFIXES):
            notes.append(f"{rel}: skipped class {cls.name}, name marks it as UI/config plumbing")
            continue
        if not _is_model(cls):
            continue

        fields = _fields(cls, enums)
        if not fields:
            continue
        if _is_envelope(fields):
            notes.append(f"{rel}: skipped class {cls.name}, shape is a result page not an entity")
            continue
        if len(fields) < 2:
            # A one-field class is a request body or a wrapper, not something
            # that warrants its own table, route, and page.
            notes.append(
                f"{rel}: skipped class {cls.name}, a single field is not a domain entity"
            )
            continue

        state_field, state_values = _state_machine(fields)
        entities.append(
            ExtractedEntity(
                name=_entity_name(cls.name),
                source_file=rel,
                fields=fields,
                description=_docstring(cls),
                confidence=Confidence.HIGH if _has_model_base(cls) else Confidence.MEDIUM,
                state_field=state_field,
                state_values=state_values,
            )
        )
    return entities


def _entity_name(class_name: str) -> str:
    """Strip DTO affixes so three projections collapse onto one entity name."""
    stem = class_name
    for prefix in DTO_PREFIXES:
        if stem.startswith(prefix) and len(stem) > len(prefix) and stem[len(prefix)].isupper():
            stem = stem[len(prefix) :]
            break
    for suffix in DTO_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem or class_name


def _base_names(cls: ast.ClassDef) -> set[str]:
    names = set()
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
        elif isinstance(base, ast.Subscript):
            inner = base.value
            if isinstance(inner, ast.Name):
                names.add(inner.id)
            elif isinstance(inner, ast.Attribute):
                names.add(inner.attr)
    return names


def _has_model_base(cls: ast.ClassDef) -> bool:
    return bool(_base_names(cls) & MODEL_BASES)


def _is_enum(cls: ast.ClassDef) -> bool:
    return bool(_base_names(cls) & ENUM_BASES)


def _decorator_names(cls: ast.ClassDef) -> set[str]:
    names = set()
    for dec in cls.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _is_model(cls: ast.ClassDef) -> bool:
    if _has_model_base(cls):
        return True
    if _decorator_names(cls) & MODEL_DECORATORS:
        return True
    # SQLAlchemy declarative bases are often locally named, so fall back to the
    # shape: a class that assigns `Column(...)` is a table whatever its base.
    return any(_call_name(_value_of(node)) in ("Column", "mapped_column") for node in cls.body)


def _value_of(node: ast.stmt) -> ast.expr | None:
    if isinstance(node, ast.AnnAssign):
        return node.value
    if isinstance(node, ast.Assign):
        return node.value
    return None


def _call_name(node: ast.expr | None) -> str:
    if not isinstance(node, ast.Call):
        return ""
    return _name_of(node.func)


def _name_of(node: ast.expr | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return ""


def _enum_values(cls: ast.ClassDef) -> list[str]:
    values: list[str] = []
    for node in cls.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                values.append(str(value))
    return values


def _docstring(cls: ast.ClassDef) -> str:
    doc = ast.get_docstring(cls) or ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def _is_envelope(fields: list[ExtractedField]) -> bool:
    names = {f.name for f in fields}
    return bool(names & ENVELOPE_FIELDS) and len(names) <= 4


def _fields(cls: ast.ClassDef, enums: dict[str, list[str]]) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if _skip_field(name, node.annotation):
                continue
            fields.append(_field(name, node.annotation, node.value, enums))
        elif isinstance(node, ast.Assign) and _call_name(node.value) in ("Column", "mapped_column"):
            for target in node.targets:
                if isinstance(target, ast.Name) and not _skip_field(target.id, None):
                    fields.append(_field(target.id, None, node.value, enums))
    return fields


def _skip_field(name: str, annotation: ast.expr | None) -> bool:
    if name.startswith("_") or name in ("model_config", "Config", "Meta", "metadata", "registry"):
        return True
    return _name_of(annotation) in ("ClassVar", "InitVar")


def _field(
    name: str,
    annotation: ast.expr | None,
    value: ast.expr | None,
    enums: dict[str, list[str]],
) -> ExtractedField:
    field = ExtractedField(name=name)
    optional = False

    if annotation is not None:
        field.type, field.enum_values, optional = _annotation_type(annotation, enums)
        field.required = not optional and not _has_default(value)
    else:
        field.required = False

    call = _call_name(value)
    if call in ("Column", "mapped_column"):
        _apply_column(field, value, enums)
    elif call == "Field":
        _apply_pydantic_field(field, value)
        if _pydantic_field_is_required(value):
            field.required = True
        elif _pydantic_field_has_default(value):
            field.required = False

    if not field.reference_entity:
        _infer_reference(field)

    field.sensitive = is_sensitive_name(name)

    return field


def _has_default(value: ast.expr | None) -> bool:
    if value is None:
        return False
    # `x: str = Field(...)` is Pydantic's spelling of "required".
    if _call_name(value) == "Field":
        return not _pydantic_field_is_required(value)
    if _call_name(value) in ("Column", "mapped_column"):
        return False
    return True


def _pydantic_field_is_required(value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    if value.args and isinstance(value.args[0], ast.Constant) and value.args[0].value is Ellipsis:
        return True
    for kw in value.keywords:
        if kw.arg == "default" and isinstance(kw.value, ast.Constant):
            return kw.value.value is Ellipsis
    return False


def _pydantic_field_has_default(value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    if value.args:
        return True
    return any(kw.arg in ("default", "default_factory") for kw in value.keywords)


def _annotation_type(
    annotation: ast.expr,
    enums: dict[str, list[str]],
) -> tuple[str, list[str], bool]:
    """Resolve an annotation to (contract type, enum values, is_optional)."""
    optional = False

    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # A string annotation (`"User"`, or a module with `from __future__ import
        # annotations`) carries the same information, just unparsed.
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except (SyntaxError, ValueError):
            return "string", [], False

    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        parts = [annotation.left, annotation.right]
        non_none = [p for p in parts if not _is_none(p)]
        optional = len(non_none) < len(parts)
        if not non_none:
            return "string", [], True
        inner_type, values, inner_optional = _annotation_type(non_none[0], enums)
        return inner_type, values, optional or inner_optional

    if isinstance(annotation, ast.Subscript):
        outer = _name_of(annotation.value)
        if outer == "Literal":
            return "string", _literal_values(annotation.slice), False
        if outer in ("Optional",):
            inner_type, values, _ = _annotation_type(annotation.slice, enums)
            return inner_type, values, True
        if outer in ("Union",):
            elements = _tuple_elements(annotation.slice)
            non_none = [e for e in elements if not _is_none(e)]
            optional = len(non_none) < len(elements)
            if not non_none:
                return "string", [], True
            inner_type, values, inner_optional = _annotation_type(non_none[0], enums)
            return inner_type, values, optional or inner_optional
        if outer in ("Annotated",):
            elements = _tuple_elements(annotation.slice)
            if elements:
                return _annotation_type(elements[0], enums)
        if outer in ("Mapped", "Final"):
            return _annotation_type(annotation.slice, enums)
        if outer in PY_ANNOTATION_TYPES:
            return PY_ANNOTATION_TYPES[outer], [], False
        return "object", [], False

    name = _name_of(annotation)
    if name in enums:
        return "string", list(enums[name]), False
    if name in PY_ANNOTATION_TYPES:
        return PY_ANNOTATION_TYPES[name], [], False
    if name in SQL_COLUMN_TYPES:
        return SQL_COLUMN_TYPES[name], [], False
    return "string", [], False


def _is_none(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    return isinstance(node, ast.Name) and node.id == "None"


def _tuple_elements(node: ast.expr) -> list[ast.expr]:
    return list(node.elts) if isinstance(node, ast.Tuple) else [node]


def _literal_values(node: ast.expr) -> list[str]:
    values = []
    for element in _tuple_elements(node):
        if isinstance(element, ast.Constant) and isinstance(element.value, (str, int)):
            values.append(str(element.value))
    return values


def _apply_column(field: ExtractedField, value: ast.expr | None, enums: dict) -> None:
    """Read type, nullability, foreign key, and size out of a `Column(...)`."""
    if not isinstance(value, ast.Call):
        return

    for arg in value.args:
        type_name = _name_of(arg)
        if type_name == "ForeignKey":
            _apply_foreign_key(field, arg)
            continue
        if type_name == "Enum":
            field.type = "string"
            field.enum_values = _sql_enum_values(arg, enums)
            continue
        if type_name in SQL_COLUMN_TYPES:
            field.type = SQL_COLUMN_TYPES[type_name]
            _apply_column_type_args(field, arg)

    for kw in value.keywords:
        if kw.arg == "nullable" and isinstance(kw.value, ast.Constant):
            field.required = kw.value.value is False
        elif kw.arg == "primary_key" and isinstance(kw.value, ast.Constant) and kw.value.value:
            field.required = True
            field.immutable = True
        elif kw.arg == "comment" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, str):
                field.description = kw.value.value
        elif kw.arg in ("default", "server_default"):
            field.required = False


def _apply_column_type_args(field: ExtractedField, arg: ast.expr) -> None:
    if not isinstance(arg, ast.Call):
        return
    ints = [a.value for a in arg.args if isinstance(a, ast.Constant) and isinstance(a.value, int)]
    if field.type in ("string", "text") and ints:
        field.constraints["maxLength"] = ints[0]
    elif field.type == "decimal" and ints:
        field.constraints["precision"] = ints[0]
        if len(ints) > 1:
            field.constraints["scale"] = ints[1]


def _sql_enum_values(arg: ast.expr, enums: dict[str, list[str]]) -> list[str]:
    if not isinstance(arg, ast.Call):
        return []
    values = [a.value for a in arg.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    if values:
        return values
    for a in arg.args:
        name = _name_of(a)
        if name in enums:
            return list(enums[name])
    return []


def _apply_foreign_key(field: ExtractedField, arg: ast.expr) -> None:
    if not isinstance(arg, ast.Call) or not arg.args:
        return
    target = arg.args[0]
    if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
        return
    table = target.value.split(".")[0]
    if table:
        field.reference_entity = _singularize(table)
        field.reference_edge = re.sub(r"_ID$", "", field.name.upper()) or "REFERENCES"


def _apply_pydantic_field(field: ExtractedField, value: ast.expr | None) -> None:
    if not isinstance(value, ast.Call):
        return
    for kw in value.keywords:
        if not isinstance(kw.value, ast.Constant):
            continue
        raw = kw.value.value
        if kw.arg == "description" and isinstance(raw, str):
            field.description = raw
        elif kw.arg == "max_length" and isinstance(raw, int):
            field.constraints["maxLength"] = raw
        elif kw.arg == "min_length" and isinstance(raw, int):
            field.constraints["minLength"] = raw
        elif kw.arg == "pattern" and isinstance(raw, str):
            field.constraints["pattern"] = raw
        elif kw.arg in ("gt", "ge") and isinstance(raw, (int, float)):
            field.constraints["min"] = raw
        elif kw.arg in ("lt", "le") and isinstance(raw, (int, float)):
            field.constraints["max"] = raw
        elif kw.arg == "frozen" and raw is True:
            field.immutable = True


def _infer_reference(field: ExtractedField) -> None:
    """Treat `<thing>_id` as a foreign key — the heuristic the docs promise."""
    lowered = field.name.lower()
    if not lowered.endswith("_id") or lowered == "_id":
        return
    target = lowered[:-3]
    if not target or target in ("uu", "u", "external", "correlation", "trace", "request"):
        return
    field.reference_entity = target
    field.reference_edge = target.upper()
    if field.type == "string":
        field.type = "uuid"


def _singularize(table: str) -> str:
    if table.endswith("ies") and len(table) > 3:
        return table[:-3] + "y"
    if table.endswith(("ses", "xes", "zes", "ches", "shes")):
        return table[:-2]
    if table.endswith("s") and not table.endswith("ss"):
        return table[:-1]
    return table


def _state_machine(fields: list[ExtractedField]) -> tuple[str, list[str]]:
    """Report the field that looks like a lifecycle state, if there is one."""
    for candidate in ("state", "status", "stage", "phase"):
        for f in fields:
            if f.name.lower() == candidate and len(f.enum_values) >= 2:
                return f.name, list(f.enum_values)
    return "", []

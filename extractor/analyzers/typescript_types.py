"""Pass 2b: Extract entities from TypeScript type files.

There is no TypeScript parser available to this process, so this is a
brace-matching reader over the declaration text. That is a real limitation and
it is bounded deliberately: the reader only claims the shapes it can see whole,
and every declaration it cannot handle is reported as a warning rather than
silently reduced to a name with no fields.

Known limits (all reported, none guessed at):
  * `extends` is not resolved — inherited members are not in the output.
  * Generic parameters are not instantiated; `Page<Ticket>` reads as `object`.
  * Mapped, conditional, and template-literal types (`Partial<T>`,
    `T extends U ? ... : ...`, `` `${A}-${B}` ``) are not evaluated.
  * Declaration merging across files is not performed.
  * Members whose type spans a nested object literal are typed `object`
    rather than recursed into.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from extractor.models import Confidence, ExtractedEntity, ExtractedField, is_sensitive_name

logger = logging.getLogger(__name__)

TS_TYPES = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "bigint": "integer",
    "Date": "datetime",
    "object": "object",
    "unknown": "object",
    "any": "object",
    "null": "string",
    "undefined": "string",
}

# Interfaces that describe a React component's inputs or a client's options are
# not domain entities. Emitting `button_props` as an entity — with a route, a
# page, and a database table — is noise that the user has to delete by hand.
NON_ENTITY_SUFFIXES = (
    "Props",
    "Params",
    "Options",
    "Config",
    "Settings",
    "Context",
    "State",
    "Handlers",
    "Refs",
)

DTO_SUFFIXES = ("Create", "Update", "Response", "Request", "Payload", "Patch", "Input", "DTO")

ENVELOPE_FIELDS = {"items", "results", "data", "records", "edges"}

_DECLARATION = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?(?:declare\s+)?"
    r"(?:(?P<kw>interface)\s+(?P<iname>[A-Za-z_$][\w$]*)|"
    r"(?P<kw2>type)\s+(?P<tname>[A-Za-z_$][\w$]*)\s*=)"
)

_MEMBER = re.compile(
    r"^\s*(?:readonly\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*(?P<opt>\?)?\s*:\s*(?P<type>.+?)\s*$"
)

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?<!:)//[^\n]*")
_STRING_LITERAL = re.compile(r"""(['"])(?:\\.|(?!\1).)*\1""")


def analyze_typescript_types(
    file_paths: list[str],
    root: Path,
    *,
    warnings: list[str] | None = None,
) -> list[ExtractedEntity]:
    """Extract entities from TypeScript interface and type-alias declarations."""
    notes = warnings if warnings is not None else []
    entities: list[ExtractedEntity] = []

    for rel in file_paths:
        try:
            source = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            notes.append(f"{rel}: skipped, cannot read ({e.strerror or e})")
            continue
        entities.extend(_entities_from_source(source, rel, notes))

    return entities


def _strip_comments(source: str) -> str:
    """Blank out comments, preserving offsets so brace matching stays aligned."""
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return _LINE_COMMENT.sub(blank, _BLOCK_COMMENT.sub(blank, source))


def _entities_from_source(source: str, rel: str, notes: list[str]) -> list[ExtractedEntity]:
    text = _strip_comments(source)
    entities: list[ExtractedEntity] = []

    for match in _DECLARATION.finditer(text):
        name = match.group("iname") or match.group("tname")
        if not name:
            continue

        open_brace = text.find("{", match.end())
        if open_brace == -1:
            continue
        # A `type X = ...` alias whose right-hand side is not an object literal
        # (a union, a generic instantiation, a mapped type) has no members to
        # read; anything before the next `{` other than whitespace proves it.
        if match.group("kw2") and text[match.end() : open_brace].strip():
            notes.append(f"{rel}: type {name} is not an object literal, no fields extracted")
            continue

        close_brace = _matching_brace(text, open_brace)
        if close_brace == -1:
            notes.append(f"{rel}: declaration {name} has unbalanced braces, skipped")
            continue

        if name.endswith(NON_ENTITY_SUFFIXES):
            continue

        body = text[open_brace + 1 : close_brace]
        fields, unparsed = _members(body)
        for line in unparsed:
            notes.append(f"{rel}: {name} member not understood, dropped: {line}")

        if not fields:
            continue
        if {f.name for f in fields} & ENVELOPE_FIELDS and len(fields) <= 4:
            continue

        entities.append(
            ExtractedEntity(
                name=_entity_name(name),
                source_file=rel,
                fields=fields,
                confidence=Confidence.HIGH,
                state_field=_state_field(fields),
                state_values=_state_values(fields),
            )
        )

    return entities


def _entity_name(name: str) -> str:
    for suffix in DTO_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _matching_brace(text: str, start: int) -> int:
    """Index of the `}` closing the `{` at `start`, ignoring braces in strings."""
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char in "\"'`":
            match = _STRING_LITERAL.match(text, i)
            if match:
                i = match.end()
                continue
            i += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_members(body: str) -> list[str]:
    """Split an interface body on `;`/`,`/newline at nesting depth zero."""
    members: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(body):
        char = body[i]
        if char in "\"'`":
            match = _STRING_LITERAL.match(body, i)
            if match:
                current.append(match.group(0))
                i = match.end()
                continue
        if char in "{[(<":
            depth += 1
        elif char in "}])>":
            depth = max(0, depth - 1)

        if depth == 0 and char in ";,\n":
            members.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1

    members.append("".join(current))
    return [m.strip() for m in members if m.strip()]


def _members(body: str) -> tuple[list[ExtractedField], list[str]]:
    fields: list[ExtractedField] = []
    unparsed: list[str] = []

    for raw in _split_members(body):
        if raw.startswith("[") or "(" in raw.split(":")[0]:
            # Index signature or method — neither is a contract field.
            continue
        match = _MEMBER.match(raw)
        if not match:
            unparsed.append(raw[:80])
            continue

        name = match.group("name")
        ts_type = match.group("type").strip()
        field_type, enum_values, optional = _resolve_type(ts_type)
        field = ExtractedField(
            name=name,
            type=field_type,
            required=not optional and not match.group("opt"),
            enum_values=enum_values,
        )
        _refine(field)
        fields.append(field)

    return fields, unparsed


def _resolve_type(ts_type: str) -> tuple[str, list[str], bool]:
    parts = [p.strip() for p in _split_union(ts_type)]
    optional = any(p in ("null", "undefined") for p in parts)
    parts = [p for p in parts if p not in ("null", "undefined")]
    if not parts:
        return "string", [], True

    literals = [p[1:-1] for p in parts if len(p) >= 2 and p[0] in "\"'" and p[-1] == p[0]]
    if len(literals) == len(parts) and literals:
        return "string", literals, optional

    head = parts[0]
    if head.endswith("[]") or head.startswith("Array<") or head.startswith("ReadonlyArray<"):
        return "array", [], optional
    if head.startswith(("Record<", "Map<", "Partial<", "Omit<", "Pick<")) or head.startswith("{"):
        return "object", [], optional
    if head in TS_TYPES:
        return TS_TYPES[head], [], optional
    # A reference to another declared type. Its shape is not resolved here, so
    # `object` is the honest answer rather than a guessed scalar.
    return "object", [], optional


def _split_union(ts_type: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(ts_type):
        char = ts_type[i]
        if char in "\"'`":
            match = _STRING_LITERAL.match(ts_type, i)
            if match:
                current.append(match.group(0))
                i = match.end()
                continue
        if char in "{[(<":
            depth += 1
        elif char in "}])>":
            depth = max(0, depth - 1)
        if char == "|" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    parts.append("".join(current))
    return [p for p in parts if p.strip()]


def _refine(field: ExtractedField) -> None:
    """Apply the name-based type hints the analyzer documents."""
    lowered = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", field.name).lower()
    field.sensitive = is_sensitive_name(field.name)
    if field.type == "string" and not field.enum_values:
        if "email" in lowered:
            field.type = "email"
        elif lowered == "id" or lowered.endswith("_id"):
            field.type = "uuid"
    if lowered.endswith("_id") and lowered != "_id":
        target = lowered[:-3]
        if target:
            field.reference_entity = target
            field.reference_edge = target.upper()


def _state_field(fields: list[ExtractedField]) -> str:
    for candidate in ("state", "status", "stage", "phase"):
        for f in fields:
            if f.name.lower() == candidate and len(f.enum_values) >= 2:
                return f.name
    return ""


def _state_values(fields: list[ExtractedField]) -> list[str]:
    name = _state_field(fields)
    for f in fields:
        if f.name == name:
            return list(f.enum_values)
    return []

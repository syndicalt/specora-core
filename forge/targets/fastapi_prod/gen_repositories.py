"""Generate the repository layer: abstract interface, memory adapter, Postgres adapter.

The repository layer is the only place the generated application touches
storage, so every property the application claims about its data has to hold
*here* or it does not hold at all. Three of those properties were false before
this generator was rewritten, and each failure only appears in production:

  1. **Bounded work per request.** `list()` issued `SELECT COUNT(*)` plus
     `LIMIT/OFFSET` on every call. Both are O(rows): the count is a full scan
     and `OFFSET n` makes the server materialise and discard n rows. On the
     hottest endpoint in the app that is a linear degradation with table size,
     which contradicts the product's central claim of supporting arbitrary
     size. Keyset (cursor) pagination replaces it — see `_list_*` below.

  2. **Atomic state transitions.** `transition()` was SELECT, decide in Python,
     UPDATE, with no transaction and no row lock. Two concurrent requests both
     read the old state, both judge the transition legal, and both write. The
     workflow guard — the product's core correctness guarantee — was therefore
     bypassable under concurrency. It is now either one conditional UPDATE or a
     locked read inside a transaction.

  3. **Adapter equivalence.** The Postgres adapter accepted `filters` and
     ignored them while the memory adapter honoured them, so the same call
     returned filtered rows in tests and unfiltered rows in production. If
     `filters` ever carried a tenant scope, that is cross-tenant disclosure.
     Both adapters now implement the same semantics, including error codes,
     ordering, NULL handling, and field validation.

The public shape is frozen by `docs/CODEGEN_CONTRACT.md` §7 and shared with the
route generator; changing a signature here breaks the generated routes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.ir.model import DomainIR, EntityIR, StateMachineIR
from forge.targets.base import (
    GeneratedFile,
    GenerationError,
    provenance_header,
)
from forge.targets.naming import class_name, repo_accessor, sql_ident
from forge.targets.typemap import pg_column_type

# Page size ceiling burned into the generated base module. Codegen contract §6
# forbids unbounded user input, and `limit` reaches the repository straight
# from a query string.
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 500

# Config names read from the generated `backend/config.py`. They are imported
# by name (not `getattr` with a fallback) so a missing setting fails loudly at
# import instead of silently running with a pool size nobody chose.
CONFIG_IMPORTS = (
    "DATABASE_POOL_MAX_SIZE",
    "DATABASE_POOL_MIN_SIZE",
    "DATABASE_STATEMENT_TIMEOUT_MS",
    "DATABASE_URL",
)


@dataclass(frozen=True)
class _EntityPlan:
    """Everything resolved at generation time for one entity's repositories.

    Resolving column names, casts, and the sort key here — once, from the IR —
    is what makes the emitted SQL safe: no caller-supplied string ever reaches
    an identifier position at runtime, it can only select from these maps.
    """

    entity: EntityIR
    cls: str
    accessor: str
    table: str
    table_ident: str
    columns: tuple[str, ...]
    column_idents: dict[str, str]
    select_list: str
    id_cast: str
    has_created_at: bool
    has_updated_at: bool
    sort_fields: tuple[str, ...]
    sort_casts: tuple[str, ...]
    state_machine: StateMachineIR | None = None
    guards: dict = field(default_factory=dict)

    @property
    def initial_state(self) -> str | None:
        return self.state_machine.initial if self.state_machine else None

    @property
    def transitions(self) -> dict:
        return dict(self.state_machine.transitions) if self.state_machine else {}

    @property
    def transition_sources(self) -> dict:
        """Target state -> the source states from which it is reachable.

        The inverse of the contract's `transitions` map. The conditional UPDATE
        needs it because `new_state` arrives at runtime while the legal source
        states for it are known now.
        """
        sources: dict[str, list[str]] = {}
        for src, targets in self.transitions.items():
            for target in targets:
                sources.setdefault(target, []).append(src)
        return sources


def generate_repositories(ir: DomainIR) -> list[GeneratedFile]:
    """Generate repository base, memory adapter, and postgres adapter."""
    if not ir.entities:
        return []
    plans = [_plan(entity, ir) for entity in ir.entities]
    return [
        _generate_base(plans),
        _generate_memory(plans),
        _generate_postgres(plans),
    ]


def _guard_map(sm: StateMachineIR | None) -> dict[tuple[str, str], list[str]]:
    if not sm:
        return {}
    return {
        (guard.from_state, guard.to_state): guard.require_fields
        for guard in sm.guards
        if guard.require_fields
    }


def _plan(entity: EntityIR, ir: DomainIR) -> _EntityPlan:
    """Resolve an entity into the concrete identifiers its repositories need.

    Raises:
        GenerationError: If the entity cannot satisfy the frozen repository
            interface. Failing here is the codegen contract §5 requirement: the
            alternative is emitting SQL that references a column the DDL never
            created and discovering it on the first request in production.
    """
    columns = tuple(f.name for f in entity.fields)
    if not columns:
        raise GenerationError(
            f"Entity {entity.fqn!r} declares no fields, so no repository can "
            f"select, insert, or filter anything. Give it fields, or drop it "
            f"from the domain."
        )

    id_field = next((f for f in entity.fields if f.name == "id"), None)
    if id_field is None:
        raise GenerationError(
            f"Entity {entity.fqn!r} has no `id` field. The repository interface "
            f"in docs/CODEGEN_CONTRACT.md §7 addresses every record by id "
            f"(get/update/delete/transition), and keyset pagination needs a "
            f"unique tiebreaker. Apply mixin/stdlib/identifiable."
        )

    # `created_at` only exists when the entity applies mixin/stdlib/timestamped.
    # The old generator wrote it unconditionally and ordered by it, so an entity
    # without the mixin generated SQL against a column the DDL never created.
    # Adapting is preferred over rejecting: an entity with no timestamps is a
    # legal contract, and `id` alone is already a total, stable sort order —
    # only the *presentation* (newest first) depends on `created_at`.
    has_created_at = "created_at" in columns
    has_updated_at = "updated_at" in columns

    sort_fields = ("created_at", "id") if has_created_at else ("id",)
    sort_casts = tuple(
        pg_column_type(
            next(f.type for f in entity.fields if f.name == name),
            next(f.constraints for f in entity.fields if f.name == name),
        )
        for name in sort_fields
    )

    id_pg_type = pg_column_type(id_field.type, id_field.constraints)
    # asyncpg infers a parameter's type from where it appears. Forcing the
    # parameter to `text` and casting server-side keeps the wire type of every
    # id and cursor value a plain string, whatever the column's declared type.
    id_cast = "" if id_pg_type == "TEXT" else f"::text::{id_pg_type}"

    # forge.ir.passes.state_machine_binding adds a `state` field whenever it
    # binds a workflow, so this only fires for an IR assembled by hand. Adding
    # it keeps the emitted SQL self-consistent instead of raising a
    # GenerationError on an entity the compiler would never have produced.
    if entity.state_machine and "state" not in columns:
        columns = columns + ("state",)

    column_idents = {name: sql_ident(name) for name in columns}

    return _EntityPlan(
        entity=entity,
        cls=class_name(entity.name, entity.domain, multi_domain=ir.multi_domain),
        accessor=repo_accessor(entity.name, entity.domain, multi_domain=ir.multi_domain),
        table=entity.table_name,
        table_ident=sql_ident(entity.table_name),
        columns=columns,
        column_idents=column_idents,
        select_list=", ".join(column_idents[name] for name in columns),
        id_cast=id_cast,
        has_created_at=has_created_at,
        has_updated_at=has_updated_at,
        sort_fields=sort_fields,
        sort_casts=sort_casts,
        state_machine=entity.state_machine,
        guards=_guard_map(entity.state_machine),
    )


def _order_by(plan: _EntityPlan) -> str:
    return ", ".join(f"{plan.column_idents[name]} DESC" for name in plan.sort_fields)


# Generated code is production code (codegen contract §6), so it gets the same
# 100-column budget as this repo. A wide entity produces a SELECT list and a
# column map that blow past it on one line.
_MAX_LINE = 100


def _wrap_literal(name: str, value: str, indent: str = "    ") -> list[str]:
    """Emit `name = <string literal>`, split on spaces when it is too wide.

    Implicit concatenation inside parentheses is used rather than a backslash
    or a runtime join, so the constant is still a compile-time literal.
    """
    single = f"{indent}{name} = {value!r}"
    if len(single) <= _MAX_LINE:
        return [single]

    budget = _MAX_LINE - len(indent) - 8
    chunks: list[str] = []
    current = ""
    for token in value.split(" "):
        candidate = f"{current} {token}" if current else token
        if current and len(candidate) > budget:
            chunks.append(current + " ")
            current = token
        else:
            current = candidate
    if current:
        chunks.append(current)
    return (
        [f"{indent}{name} = ("]
        + [f"{indent}    {chunk!r}" for chunk in chunks]
        + [f"{indent})"]
    )


def _wrap_collection(
    name: str,
    items: list[str],
    open_s: str,
    close_s: str,
    indent: str = "    ",
) -> list[str]:
    """Emit `name = <open><items><close>`, packed across lines when too wide."""
    single = f"{indent}{name} = {open_s}{', '.join(items)}{close_s}"
    if len(single) <= _MAX_LINE:
        return [single]

    budget = _MAX_LINE - len(indent) - 4
    lines = [f"{indent}{name} = {open_s}"]
    current = ""
    for item in items:
        piece = f"{item},"
        if current and len(current) + 1 + len(piece) > budget:
            lines.append(f"{indent}    {current}")
            current = piece
        else:
            current = f"{current} {piece}" if current else piece
    if current:
        lines.append(f"{indent}    {current}")
    lines.append(f"{indent}{close_s}")
    return lines


def _fstring_lines(text: str, indent: str) -> list[str]:
    """Emit `text` as one or more adjacent single-quoted f-string literals.

    Raises:
        GenerationError: If `text` contains a single quote, which would end the
            literal early and emit unparseable Python.
    """
    if "'" in text:
        raise GenerationError(
            f"Cannot emit {text!r} as a single-quoted f-string literal; it "
            f"contains a quote character. This is a generator bug, not a "
            f"contract error."
        )
    budget = _MAX_LINE - len(indent) - 4
    chunks: list[str] = []
    current = ""
    for token in text.split(" "):
        candidate = f"{current} {token}" if current else token
        if current and len(candidate) > budget:
            chunks.append(current + " ")
            current = token
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [f"{indent}f'{chunk}'" for chunk in chunks]


def _wrap_dict(name: str, mapping: dict, indent: str = "    ") -> list[str]:
    """Emit a dict literal, packed across lines when it is too wide."""
    return _wrap_collection(
        name,
        [f"{k!r}: {v!r}" for k, v in mapping.items()],
        "{",
        "}",
        indent=indent,
    )


def _base_symbols(plans: list[_EntityPlan]) -> list[str]:
    """Names an adapter module imports from the generated base module.

    Built from what the adapters actually emit rather than a fixed preamble:
    codegen contract §3, and the generated app lints its own output.
    """
    symbols = ["ListPage", "clamp_limit", "decode_cursor", "encode_cursor",
               "reject_unknown_fields"]
    if any(p.state_machine for p in plans):
        symbols.append("TransitionResult")
    symbols += [f"{p.cls}Repository" for p in plans]
    return sorted(symbols)


# =============================================================================
# backend/repositories/base.py
# =============================================================================


def _generate_base(plans: list[_EntityPlan]) -> GeneratedFile:
    fqns = ", ".join(p.entity.fqn for p in plans)
    header = provenance_header("python", fqns, "Abstract repository interfaces")

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import base64",
        "import json",
        "from abc import ABC, abstractmethod",
        "from collections.abc import Iterable, Sequence",
        "from dataclasses import dataclass",
        "from datetime import date, datetime",
        "from typing import Any",
        "",
        "",
        f"DEFAULT_PAGE_LIMIT = {DEFAULT_PAGE_LIMIT}",
        f"MAX_PAGE_LIMIT = {MAX_PAGE_LIMIT}",
        "",
        "",
        "class RepositoryError(ValueError):",
        '    """A repository rejected the caller\'s input.',
        "",
        "    Subclasses of ValueError so a route layer that only catches",
        "    ValueError still maps them to a 4xx rather than letting them",
        "    escape as a 500.",
        '    """',
        "",
        "",
        "class InvalidCursorError(RepositoryError):",
        '    """The pagination cursor was not one this repository issued.',
        "",
        "    Cursors are opaque and arrive from a query string, so a truncated,",
        "    hand-edited, or stale cursor is ordinary client input. It must",
        "    surface as a 400, never as an unhandled decode error.",
        '    """',
        "",
        "",
        "class UnknownFieldError(RepositoryError):",
        '    """A filter key or write payload named a column the entity lacks.',
        "",
        "    Rejecting is deliberate. Silently dropping an unrecognised filter",
        "    key widens the result set — if that key carried a tenant scope, the",
        "    response leaks other tenants\' rows. Silently dropping an",
        "    unrecognised write key discards data the caller believes it saved.",
        '    """',
        "",
        "",
        "@dataclass",
        "class ListPage:",
        '    """One page of records plus the cursor that continues it.',
        "",
        "    `next_cursor` is None when the page is the last one.",
        '    """',
        "",
        "    items: list[dict]",
        "    next_cursor: str | None = None",
        "",
        "",
        "@dataclass",
        "class TransitionResult:",
        '    """Outcome of a state transition attempt.',
        "",
        "    `error` is None on success, otherwise one of the stable codes",
        '    "not_found", "invalid_transition", or "guard_failed". The three are',
        "    kept distinct because they map to different HTTP statuses; the",
        "    previous `dict | None` return collapsed them into one 422.",
        '    """',
        "",
        "    record: dict | None = None",
        "    error: str | None = None",
        "",
        "",
        "def clamp_limit(limit: int) -> int:",
        '    """Force a page size into [1, MAX_PAGE_LIMIT].',
        "",
        "    `limit` reaches here straight from a query string, and an",
        "    unbounded page size is an unauthenticated way to ask the database",
        "    for the whole table.",
        '    """',
        "    try:",
        "        value = int(limit)",
        "    except (TypeError, ValueError):",
        "        return DEFAULT_PAGE_LIMIT",
        "    return max(1, min(value, MAX_PAGE_LIMIT))",
        "",
        "",
        "def _cursor_scalar(value: Any) -> str:",
        "    if isinstance(value, (datetime, date)):",
        "        return value.isoformat()",
        "    return str(value)",
        "",
        "",
        "def encode_cursor(values: Sequence[Any]) -> str:",
        '    """Encode sort-key values as an opaque, URL-safe cursor.',
        "",
        "    Opaque on purpose: the encoding is an implementation detail of the",
        "    sort order, and clients that parse it would break the moment the",
        "    order changes.",
        '    """',
        '    payload = json.dumps(list(values), default=_cursor_scalar, separators=(",", ":"))',
        '    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")',
        '    return encoded.rstrip("=")',
        "",
        "",
        "def decode_cursor(cursor: str, *, arity: int) -> list[Any]:",
        '    """Decode a cursor back into its sort-key values.',
        "",
        "    Raises:",
        "        InvalidCursorError: On anything this repository did not issue.",
        '    """',
        '    padded = cursor + "=" * (-len(cursor) % 4)',
        "    try:",
        '        raw = base64.urlsafe_b64decode(padded.encode("ascii"))',
        '        values = json.loads(raw.decode("utf-8"))',
        "    except (ValueError, TypeError) as exc:",
        "        raise InvalidCursorError(",
        '            f"Malformed pagination cursor: {cursor!r}"',
        "        ) from exc",
        "    if not isinstance(values, list) or len(values) != arity:",
        "        raise InvalidCursorError(",
        '            f"Pagination cursor {cursor!r} does not match this collection\'s sort key."',
        "        )",
        "    return values",
        "",
        "",
        "def reject_unknown_fields(",
        "    keys: Iterable[str],",
        "    allowed: frozenset,",
        "    entity: str,",
        ") -> None:",
        '    """Validate caller-supplied keys against an entity\'s column allowlist.',
        "",
        "    Every identifier the adapters put into SQL is looked up in a",
        "    generation-time map keyed by these names, so an unvalidated key",
        "    cannot reach an identifier position — but it can still silently",
        "    widen a filter or drop a write, which is why it is an error.",
        "",
        "    Raises:",
        "        UnknownFieldError: If any key is outside the allowlist.",
        '    """',
        "    unknown = sorted(k for k in keys if k not in allowed)",
        "    if unknown:",
        "        raise UnknownFieldError(",
        '            f"{entity}: unknown field(s) {unknown}. "',
        '            f"Known fields: {sorted(allowed)}."',
        "        )",
        "",
        "",
    ]

    for plan in plans:
        lines.append(f"class {plan.cls}Repository(ABC):")
        lines.append(f'    """Repository interface for {plan.entity.name}.')
        lines.append("")
        lines.append("    Frozen by docs/CODEGEN_CONTRACT.md §7 — the generated routes are")
        lines.append("    written against exactly these signatures.")
        lines.append('    """')
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def list(")
        lines.append("        self,")
        lines.append("        *,")
        lines.append(f"        limit: int = {DEFAULT_PAGE_LIMIT},")
        lines.append("        cursor: str | None = None,")
        lines.append("        filters: dict[str, Any] | None = None,")
        lines.append("    ) -> ListPage: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def get(self, id: str) -> dict | None: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def create(self, data: dict) -> dict: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def update(self, id: str, data: dict) -> dict | None: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def delete(self, id: str) -> bool: ...")
        lines.append("")
        if plan.state_machine:
            lines.append("    @abstractmethod")
            lines.append(
                "    async def transition("
                "self, id: str, new_state: str) -> TransitionResult: ..."
            )
            lines.append("")
        lines.append("")

    lines.append("# Repository provider factories — wire to config")
    lines.append("# Import the concrete adapter based on DATABASE_BACKEND")
    lines.append("")
    for plan in plans:
        lines.append(f"def {plan.accessor}() -> {plan.cls}Repository:")
        lines.append("    from backend.config import DATABASE_BACKEND")
        lines.append('    if DATABASE_BACKEND == "postgres":')
        lines.append(
            f"        from backend.repositories.postgres import Postgres{plan.cls}Repository"
        )
        lines.append(f"        return Postgres{plan.cls}Repository()")
        lines.append(f"    from backend.repositories.memory import Memory{plan.cls}Repository")
        lines.append(f"    return Memory{plan.cls}Repository()")
        lines.append("")
        lines.append("")

    return GeneratedFile(
        path="backend/repositories/base.py",
        content="\n".join(lines),
        provenance=fqns,
    )


# =============================================================================
# backend/repositories/memory.py
# =============================================================================


def _generate_memory(plans: list[_EntityPlan]) -> GeneratedFile:
    fqns = ", ".join(p.entity.fqn for p in plans)
    header = provenance_header("python", fqns, "In-memory repository adapters (dev/test)")

    store_entries = ",\n".join(f'    "{p.entity.name}": {{}}' for p in plans)

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import uuid",
    ]
    if any(p.has_created_at or p.has_updated_at for p in plans):
        lines.append("from datetime import datetime, timezone")
    lines.extend([
        "from typing import Any",
        "",
        "from backend.repositories.base import (",
        "    " + ",\n    ".join(_base_symbols(plans)) + ",",
        ")",
        "",
        "",
        "# Stores live at module scope rather than on each repository class. The",
        "# adapter is process-global by design — every request must see the same",
        "# rows — but a class attribute could not be cleared without reaching into",
        "# each class, which is why test runs used to accumulate rows across cases",
        "# and a count assertion passed alone and failed in the suite.",
        "_STORES: dict[str, dict[str, dict]] = {",
        store_entries + ",",
        "}",
        "",
        "",
        "def reset_stores() -> None:",
        '    """Empty every in-memory store, in place.',
        "",
        "    Call between tests. Clearing in place (rather than rebinding) keeps",
        "    the aliases held by each repository class valid.",
        '    """',
        "    for store in _STORES.values():",
        "        store.clear()",
        "",
        "",
    ])

    for plan in plans:
        lines.extend(_memory_class(plan))
        lines.append("")

    return GeneratedFile(
        path="backend/repositories/memory.py",
        content="\n".join(lines),
        provenance=fqns,
    )


def _memory_class(plan: _EntityPlan) -> list[str]:
    name = plan.entity.name
    sort_repr = ", ".join(repr(c) for c in plan.sort_fields)

    lines = [
        f"class Memory{plan.cls}Repository({plan.cls}Repository):",
        f'    """In-memory adapter for {name}.',
        "",
        "    Semantics are identical to the Postgres adapter by construction:",
        "    same ordering, same NULL handling, same field allowlist, same",
        "    transition error codes. Divergence between the two is what makes",
        "    a green test suite compatible with a broken deployment.",
        '    """',
        "",
        f'    _store = _STORES["{name}"]',
    ]
    lines += _wrap_collection(
        "_FIELDS", [repr(c) for c in plan.columns], "frozenset({", "})"
    )
    lines += [
        f"    _SORT_FIELDS = ({sort_repr},)" if len(plan.sort_fields) == 1
        else f"    _SORT_FIELDS = ({sort_repr})",
        "",
        "    @classmethod",
        "    def _sort_key(cls, record: dict) -> tuple:",
        f'        # Mirrors SQL `ORDER BY {_order_by(plan)}`. Postgres sorts NULLs',
        "        # first under DESC, so a missing value must outrank every present",
        "        # one here too.",
        "        return tuple(",
        '            (1, "") if record.get(f) is None else (0, record[f])',
        "            for f in cls._SORT_FIELDS",
        "        )",
        "",
        "    async def list(",
        "        self,",
        "        *,",
        f"        limit: int = {DEFAULT_PAGE_LIMIT},",
        "        cursor: str | None = None,",
        "        filters: dict[str, Any] | None = None,",
        "    ) -> ListPage:",
        "        limit = clamp_limit(limit)",
        "        items = [dict(r) for r in self._store.values()]",
        "        if filters:",
        f'            reject_unknown_fields(filters, self._FIELDS, "{name}")',
        "            items = [",
        "                r for r in items",
        "                if all(r.get(k) == v for k, v in filters.items())",
        "            ]",
        "        items.sort(key=self._sort_key, reverse=True)",
        "        if cursor is not None:",
        "            after = decode_cursor(cursor, arity=len(self._SORT_FIELDS))",
        "            bound = self._sort_key(dict(zip(self._SORT_FIELDS, after)))",
        "            # A SQL row comparison whose left side contains NULL yields",
        "            # NULL, which drops the row. The Postgres adapter therefore",
        "            # never returns a NULL-sorted row on a continuation page, so",
        "            # neither may this one.",
        "            items = [",
        "                r for r in items",
        "                if all(r.get(f) is not None for f in self._SORT_FIELDS)",
        "                and self._sort_key(r) < bound",
        "            ]",
        "        # One row beyond the page is enough to know another page exists,",
        "        # which is why no COUNT(*) is needed to fill in next_cursor.",
        "        has_more = len(items) > limit",
        "        page = items[:limit]",
        "        next_cursor = (",
        "            encode_cursor([page[-1].get(f) for f in self._SORT_FIELDS])",
        "            if has_more and page else None",
        "        )",
        "        return ListPage(items=page, next_cursor=next_cursor)",
        "",
        "    async def get(self, id: str) -> dict | None:",
        "        record = self._store.get(id)",
        "        # Copy: the caller must not be handed a live reference into the",
        "        # store, or its response-shaping mutations become persisted rows.",
        "        return dict(record) if record is not None else None",
        "",
        "    async def create(self, data: dict) -> dict:",
        f'        reject_unknown_fields(data, self._FIELDS, "{name}")',
        "        record = dict(data)",
        '        record.setdefault("id", str(uuid.uuid4()))',
    ]

    if plan.has_created_at or plan.has_updated_at:
        lines.append("        now = datetime.now(timezone.utc).isoformat()")
    if plan.has_created_at:
        lines.append('        record["created_at"] = now')
    if plan.has_updated_at:
        lines.append('        record["updated_at"] = now')
    if plan.initial_state:
        lines.append(f'        record.setdefault("state", "{plan.initial_state}")')

    lines.extend([
        '        self._store[record["id"]] = record',
        "        return dict(record)",
        "",
        "    async def update(self, id: str, data: dict) -> dict | None:",
        f'        reject_unknown_fields(data, self._FIELDS, "{name}")',
        "        record = self._store.get(id)",
        "        if record is None:",
        "            return None",
        "        # `id` is the store key and the Postgres primary key; letting an",
        "        # update move it would orphan the row under its old key.",
        '        record.update({k: v for k, v in data.items() if k != "id"})',
    ])
    if plan.has_updated_at:
        lines.append('        record["updated_at"] = datetime.now(timezone.utc).isoformat()')
    lines.extend([
        "        return dict(record)",
        "",
        "    async def delete(self, id: str) -> bool:",
        "        return self._store.pop(id, None) is not None",
        "",
    ])

    if plan.state_machine:
        lines.extend(_memory_transition(plan))

    return lines


def _memory_transition(plan: _EntityPlan) -> list[str]:
    lines = [
        "    async def transition(self, id: str, new_state: str) -> TransitionResult:",
        "        # No await between the read and the write, so the event loop",
        "        # cannot interleave another transition in between. That is the",
        "        # in-memory equivalent of the Postgres adapter's row lock.",
        "        record = self._store.get(id)",
        "        if record is None:",
        '            return TransitionResult(record=None, error="not_found")',
        '        current = record.get("state") or ""',
    ]
    lines += _wrap_dict("valid_transitions", plan.transitions, indent="        ")
    lines += [
        "        if new_state not in valid_transitions.get(current, ()):",
        '            return TransitionResult(record=None, error="invalid_transition")',
    ]
    if plan.guards:
        lines += _wrap_dict("transition_guards", plan.guards, indent="        ")
        lines += [
            "        for field in transition_guards.get((current, new_state), []):",
            "            if record.get(field) in (None, '', [], {}):",
            '                return TransitionResult(record=None, error="guard_failed")',
        ]
    lines.append('        record["state"] = new_state')
    if plan.has_updated_at:
        lines.append('        record["updated_at"] = datetime.now(timezone.utc).isoformat()')
    lines.extend([
        "        return TransitionResult(record=dict(record), error=None)",
        "",
    ])
    return lines


# =============================================================================
# backend/repositories/postgres.py
# =============================================================================


def _generate_postgres(plans: list[_EntityPlan]) -> GeneratedFile:
    fqns = ", ".join(p.entity.fqn for p in plans)
    header = provenance_header("python", fqns, "PostgreSQL repository adapters (asyncpg)")

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import uuid",
    ]
    if any(p.has_created_at or p.has_updated_at for p in plans):
        lines.append("from datetime import datetime, timezone")
    lines.extend([
        "from typing import Any",
        "",
        "import asyncpg",
        "",
        "from backend.config import (",
        "    " + ",\n    ".join(CONFIG_IMPORTS) + ",",
        ")",
        "from backend.repositories.base import (",
        "    " + ",\n    ".join(_base_symbols(plans)) + ",",
        ")",
        "",
        "",
        "# Connection pool — initialized on first use",
        "_pool: asyncpg.Pool | None = None",
        "",
        "",
        "async def get_pool() -> asyncpg.Pool:",
        '    """Return the process-wide asyncpg pool, creating it on first use."""',
        "    global _pool",
        "    if _pool is None:",
        "        _pool = await asyncpg.create_pool(",
        "            DATABASE_URL,",
        "            min_size=DATABASE_POOL_MIN_SIZE,",
        "            max_size=DATABASE_POOL_MAX_SIZE,",
        "            # Server-side cap. Postgres cancels a statement that exceeds",
        "            # it and the connection returns to the pool, so one",
        "            # pathological query cannot hold a slot for the process'",
        "            # lifetime. max_size is the per-container concurrency",
        "            # ceiling, so a pinned connection is a real outage.",
        "            server_settings={",
        '                "statement_timeout": str(DATABASE_STATEMENT_TIMEOUT_MS),',
        "            },",
        "        )",
        "    return _pool",
        "",
        "",
    ])

    for plan in plans:
        lines.extend(_postgres_class(plan))
        lines.append("")

    return GeneratedFile(
        path="backend/repositories/postgres.py",
        content="\n".join(lines),
        provenance=fqns,
    )


def _postgres_class(plan: _EntityPlan) -> list[str]:
    name = plan.entity.name
    sort_repr = ", ".join(repr(c) for c in plan.sort_fields)

    sql_get = (
        f"SELECT {plan.select_list} FROM {plan.table_ident} "
        f'WHERE {plan.column_idents["id"]} = $1{plan.id_cast}'
    )
    sql_delete = (
        f"DELETE FROM {plan.table_ident} "
        f'WHERE {plan.column_idents["id"]} = $1{plan.id_cast} '
        f'RETURNING {plan.column_idents["id"]}'
    )
    sql_exists = (
        f"SELECT 1 FROM {plan.table_ident} "
        f'WHERE {plan.column_idents["id"]} = $1{plan.id_cast}'
    )

    lines = [
        f"class Postgres{plan.cls}Repository({plan.cls}Repository):",
        f'    """PostgreSQL adapter for {name}.',
        "",
        "    Every identifier that reaches SQL comes from _COLUMN_IDENTS, which",
        "    is built at generation time from the contract and quoted by",
        "    forge.targets.naming.sql_ident. Caller-supplied strings only ever",
        "    become bind parameters.",
        '    """',
        "",
        f"    _TABLE = {plan.table_ident!r}",
    ]
    lines += _wrap_literal("_SELECT", plan.select_list)
    lines += _wrap_dict("_COLUMN_IDENTS", plan.column_idents)
    lines += [
        # Derived rather than restated: the allowlist and the identifier map
        # must never be able to drift apart.
        "    _FIELDS = frozenset(_COLUMN_IDENTS)",
        f"    _SORT_FIELDS = ({sort_repr},)" if len(plan.sort_fields) == 1
        else f"    _SORT_FIELDS = ({sort_repr})",
    ]
    lines += _wrap_literal("_SQL_GET", sql_get)
    lines += _wrap_literal("_SQL_DELETE", sql_delete)
    if plan.state_machine:
        lines.extend(_postgres_transition_constants(plan, sql_exists))
    lines.append("")

    lines.extend(_postgres_list(plan))
    lines.extend([
        "    async def get(self, id: str) -> dict | None:",
        "        pool = await get_pool()",
        "        async with pool.acquire() as conn:",
        "            row = await conn.fetchrow(self._SQL_GET, id)",
        "        return dict(row) if row is not None else None",
        "",
    ])
    lines.extend(_postgres_create(plan))
    lines.extend(_postgres_update(plan))
    lines.extend([
        "    async def delete(self, id: str) -> bool:",
        "        pool = await get_pool()",
        "        async with pool.acquire() as conn:",
        "            row = await conn.fetchrow(self._SQL_DELETE, id)",
        "        return row is not None",
        "",
    ])

    if plan.state_machine:
        lines.extend(_postgres_transition(plan))

    return lines


def _postgres_list(plan: _EntityPlan) -> list[str]:
    name = plan.entity.name
    order_by = _order_by(plan)

    # The keyset predicate. A row comparison over the whole sort key is a single
    # index-orderable expression, so the server seeks straight to the cursor
    # position instead of counting past it the way OFFSET does.
    last = len(plan.sort_fields) - 1
    placeholders = ", ".join(
        f"${{len(params)}}::text::{cast}" if i == last
        else f"${{len(params) - {last - i}}}::text::{cast}"
        for i, cast in enumerate(plan.sort_casts)
    )
    if last == 0:
        keyset = f"{plan.column_idents[plan.sort_fields[0]]} < {placeholders}"
    else:
        left = ", ".join(plan.column_idents[f] for f in plan.sort_fields)
        keyset = f"({left}) < ({placeholders})"

    return [
        "    async def list(",
        "        self,",
        "        *,",
        f"        limit: int = {DEFAULT_PAGE_LIMIT},",
        "        cursor: str | None = None,",
        "        filters: dict[str, Any] | None = None,",
        "    ) -> ListPage:",
        "        limit = clamp_limit(limit)",
        "        params: list[Any] = []",
        "        clauses: list[str] = []",
        "        if filters:",
        f'            reject_unknown_fields(filters, self._FIELDS, "{name}")',
        "            for key, value in filters.items():",
        "                params.append(value)",
        "                # Identifier from the generation-time allowlist, value",
        "                # from a bind parameter — the caller's key never reaches",
        "                # the SQL text.",
        '                clauses.append(f"{self._COLUMN_IDENTS[key]} = ${len(params)}")',
        "        if cursor is not None:",
        "            after = decode_cursor(cursor, arity=len(self._SORT_FIELDS))",
        "            params.extend(after)",
        "            # Cursor values are transported as text and cast server-side",
        "            # so the wire type does not depend on the column's type.",
        "            clauses.append(",
        *_fstring_lines(keyset, "                "),
        "            )",
        "        # Ask for one row past the page: its presence is what proves a",
        "        # next page exists, replacing SELECT COUNT(*) entirely.",
        "        params.append(limit + 1)",
        '        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""',
        "        sql = (",
        '            f"SELECT {self._SELECT} FROM {self._TABLE}{where} "',
        f'            f\'ORDER BY {order_by} LIMIT ${{len(params)}}\'',
        "        )",
        "        pool = await get_pool()",
        "        async with pool.acquire() as conn:",
        "            rows = await conn.fetch(sql, *params)",
        "        items = [dict(r) for r in rows]",
        "        has_more = len(items) > limit",
        "        page = items[:limit]",
        "        next_cursor = (",
        "            encode_cursor([page[-1].get(f) for f in self._SORT_FIELDS])",
        "            if has_more and page else None",
        "        )",
        "        return ListPage(items=page, next_cursor=next_cursor)",
        "",
    ]


def _postgres_create(plan: _EntityPlan) -> list[str]:
    name = plan.entity.name
    lines = [
        "    async def create(self, data: dict) -> dict:",
        f'        reject_unknown_fields(data, self._FIELDS, "{name}")',
        "        record = dict(data)",
        '        record.setdefault("id", str(uuid.uuid4()))',
    ]
    if plan.has_created_at or plan.has_updated_at:
        lines.append("        now = datetime.now(timezone.utc)")
    if plan.has_created_at:
        lines.append('        record["created_at"] = now')
    if plan.has_updated_at:
        lines.append('        record["updated_at"] = now')
    if plan.initial_state:
        lines.append(f'        record.setdefault("state", "{plan.initial_state}")')
    lines.extend([
        "        keys = list(record)",
        '        columns = ", ".join(self._COLUMN_IDENTS[k] for k in keys)',
        '        placeholders = ", ".join(f"${i}" for i in range(1, len(keys) + 1))',
        "        sql = (",
        '            f"INSERT INTO {self._TABLE} ({columns}) "',
        '            f"VALUES ({placeholders}) RETURNING {self._SELECT}"',
        "        )",
        "        pool = await get_pool()",
        "        async with pool.acquire() as conn:",
        "            row = await conn.fetchrow(sql, *(record[k] for k in keys))",
        "        return dict(row)",
        "",
    ])
    return lines


def _postgres_update(plan: _EntityPlan) -> list[str]:
    name = plan.entity.name
    id_ident = plan.column_idents["id"]
    lines = [
        "    async def update(self, id: str, data: dict) -> dict | None:",
        f'        reject_unknown_fields(data, self._FIELDS, "{name}")',
        "        # `id` is the primary key; letting an update move it would",
        "        # orphan the row under its old identity.",
        '        payload = {k: v for k, v in data.items() if k != "id"}',
    ]
    if plan.has_updated_at:
        lines.append("        payload[\"updated_at\"] = datetime.now(timezone.utc)")
    lines.extend([
        "        if not payload:",
        "            return await self.get(id)",
        "        assignments: list[str] = []",
        "        params: list[Any] = []",
        "        for key, value in payload.items():",
        "            params.append(value)",
        '            assignments.append(f"{self._COLUMN_IDENTS[key]} = ${len(params)}")',
        "        params.append(id)",
        "        sql = (",
        '            f"UPDATE {self._TABLE} SET " + ", ".join(assignments)',
        f'            + f\' WHERE {id_ident} = ${{len(params)}}{plan.id_cast} \'',
        '            + f"RETURNING {self._SELECT}"',
        "        )",
        "        pool = await get_pool()",
        "        async with pool.acquire() as conn:",
        "            row = await conn.fetchrow(sql, *params)",
        "        return dict(row) if row is not None else None",
        "",
    ])
    return lines


def _postgres_transition_constants(plan: _EntityPlan, sql_exists: str) -> list[str]:
    """Emit the SQL constants `transition()` uses, in two flavours.

    See `_postgres_transition` for why there are two.
    """
    id_ident = plan.column_idents["id"]
    state_ident = plan.column_idents["state"]

    set_clause = f"{state_ident} = $1"
    param_index = 2
    if plan.has_updated_at:
        set_clause += f", {plan.column_idents['updated_at']} = ${param_index}"
        param_index += 1
    id_param = f"${param_index}{plan.id_cast}"

    if plan.guards:
        sql_lock = (
            f"SELECT {plan.select_list} FROM {plan.table_ident} "
            f"WHERE {id_ident} = $1{plan.id_cast} FOR UPDATE"
        )
        sql_apply = (
            f"UPDATE {plan.table_ident} SET {set_clause} "
            f"WHERE {id_ident} = {id_param} RETURNING {plan.select_list}"
        )
        return (
            _wrap_literal("_SQL_TRANSITION_LOCK", sql_lock)
            + _wrap_literal("_SQL_TRANSITION_APPLY", sql_apply)
        )

    sql_apply = (
        f"UPDATE {plan.table_ident} SET {set_clause} "
        f"WHERE {id_ident} = {id_param} "
        f"AND {state_ident} = ANY(${param_index + 1}::text[]) "
        f"RETURNING {plan.select_list}"
    )
    return (
        _wrap_literal("_SQL_TRANSITION_APPLY", sql_apply)
        + _wrap_literal("_SQL_EXISTS", sql_exists)
        + _wrap_dict("_TRANSITION_SOURCES", plan.transition_sources)
    )


def _postgres_transition(plan: _EntityPlan) -> list[str]:
    """Emit an atomic `transition()`.

    Two shapes, both atomic, chosen by whether any guard needs field inspection:

      * No guards — one conditional UPDATE whose WHERE pins the source state.
        Postgres re-reads the row under the update's own lock, so a concurrent
        transition either finds the state already moved and matches zero rows,
        or wins and the loser matches zero rows. One round trip on success.

      * Guards present — SELECT ... FOR UPDATE inside an explicit transaction.
        The guard predicate has to be evaluated in Python to stay bit-identical
        with the memory adapter (a field is unsatisfied when it is NULL, "",
        [], or {} — semantics SQL cannot express uniformly across column
        types). The row lock held for the transaction's duration serialises
        concurrent transitions on the same record, which is what the old
        unlocked read-check-write lacked.
    """
    if plan.guards:
        args = ["new_state"]
        if plan.has_updated_at:
            args.append("datetime.now(timezone.utc)")
        args.append("id")

        body = "                "
        lines = [
            "    async def transition(self, id: str, new_state: str) -> TransitionResult:",
            "        pool = await get_pool()",
            "        async with pool.acquire() as conn:",
            "            # The lock is held until this block exits, so a second",
            "            # transition on the same record blocks on the SELECT",
            "            # rather than racing past the guard check.",
            "            async with conn.transaction():",
            f"{body}row = await conn.fetchrow(self._SQL_TRANSITION_LOCK, id)",
            f"{body}if row is None:",
            f'{body}    return TransitionResult(record=None, error="not_found")',
            f"{body}record = dict(row)",
            f'{body}current = record.get("state") or ""',
        ]
        lines += _wrap_dict("valid_transitions", plan.transitions, indent=body)
        lines += [
            f"{body}if new_state not in valid_transitions.get(current, ()):",
            f"{body}    return TransitionResult(",
            f'{body}        record=None, error="invalid_transition"',
            f"{body}    )",
        ]
        lines += _wrap_dict("transition_guards", plan.guards, indent=body)
        lines += [
            f"{body}for field in transition_guards.get((current, new_state), []):",
            f"{body}    if record.get(field) in (None, '', [], {{}}):",
            f"{body}        return TransitionResult(",
            f'{body}            record=None, error="guard_failed"',
            f"{body}        )",
            f"{body}updated = await conn.fetchrow(",
            f"{body}    self._SQL_TRANSITION_APPLY,",
            f"{body}    " + ", ".join(args) + ",",
            f"{body})",
            "        return TransitionResult(record=dict(updated), error=None)",
            "",
        ]
        return lines

    args = ["new_state"]
    if plan.has_updated_at:
        args.append("datetime.now(timezone.utc)")
    args.extend(["id", "sources"])

    return [
        "    async def transition(self, id: str, new_state: str) -> TransitionResult:",
        "        sources = self._TRANSITION_SOURCES.get(new_state)",
        "        pool = await get_pool()",
        "        async with pool.acquire() as conn:",
        "            if sources:",
        "                # Read-check-write collapsed into one statement: the",
        "                # source-state predicate is evaluated under the same row",
        "                # lock the write takes, so two concurrent transitions",
        "                # cannot both observe the old state and both succeed.",
        "                updated = await conn.fetchrow(",
        "                    self._SQL_TRANSITION_APPLY,",
        "                    " + ", ".join(args) + ",",
        "                )",
        "                if updated is not None:",
        "                    return TransitionResult(record=dict(updated), error=None)",
        "            # Only on the failure path: distinguish a missing record from",
        "            # a legal record in the wrong state, which the caller maps to",
        "            # 404 and 409 respectively.",
        "            exists = await conn.fetchval(self._SQL_EXISTS, id)",
        "        return TransitionResult(",
        "            record=None,",
        '            error="not_found" if exists is None else "invalid_transition",',
        "        )",
        "",
    ]

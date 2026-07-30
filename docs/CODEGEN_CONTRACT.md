# Codegen Contract

Binding rules for every generator in `forge/targets/`. These exist because each
was violated somewhere in the codebase and the violation shipped.

## 1. Never derive an identifier by hand

Import from `forge.targets.naming`. Do not write `"".join(p.capitalize() ...)`,
`name + "s"`, or `path.replace("/", "_")` in a generator again.

| Need | Use |
|---|---|
| Python class stem | `class_name(entity.name, entity.domain, multi_domain=ir.multi_domain)` |
| SQL table | `entity.table_name` (already set by the IR pass) |
| Route module stem | `module_slug(entity.name, entity.domain, multi_domain=ir.multi_domain)` |
| Repo factory fn | `repo_accessor(entity.name, entity.domain, multi_domain=ir.multi_domain)` |
| Any identifier from free text | `py_identifier(raw)` |
| SQL identifier | `sql_ident(name)` — always quotes |
| SQL literal | `sql_literal(value)` — always escapes |

`multi_domain` must be threaded through from `ir.multi_domain`. Single-domain
output must stay byte-identical to today; multi-domain output must namespace.

## 2. Never hand-roll a type mapping

Import from `forge.targets.typemap`: `py_type`, `ts_type`, `pg_column_type`,
`required_imports`. There must be exactly one mapping table in the repo.

`decimal` is exact (Decimal / NUMERIC(p,s) / JSON string). `number` is inexact
(float / DOUBLE PRECISION / JSON number). Do not conflate them.

## 3. Emit only the imports you use

Build the import block from `required_imports(...)` over the types actually
present. A generated module with unused imports fails the generated app's own
lint gate.

## 4. Generated Python must parse

`FastAPIProductionGenerator.generate()` runs `validate_generated_files()`, which
`ast.parse`s every `.py` and rejects duplicate output paths. If your generator
can emit a construct that doesn't parse, that is a bug in the generator, not a
reason to relax the gate.

## 5. Fail at generation time, not deploy time

If a contract cannot produce valid output — an endpoint shape you don't handle,
a value you cannot render — raise `GenerationError` from
`forge.targets.base`. Never emit a stub that returns `{"message": "not
implemented"}` with a 200, and never silently drop the construct.

## 6. Generated code is production code

It is deployed as-is into containers built by CI. It gets the same standard as
this repo: no `except Exception: pass`, no unbounded user input, no secrets
with usable defaults, no `SELECT *`, no fail-open authorization.

## 7. Repository interface (frozen — B and C must match exactly)

```python
class <Cls>Repository(ABC):
    @abstractmethod
    async def list(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> ListPage: ...

    @abstractmethod
    async def get(self, id: str) -> dict | None: ...

    @abstractmethod
    async def create(self, data: dict) -> dict: ...

    @abstractmethod
    async def update(self, id: str, data: dict) -> dict | None: ...

    @abstractmethod
    async def delete(self, id: str) -> bool: ...

    # only when the entity binds a workflow
    @abstractmethod
    async def transition(self, id: str, new_state: str) -> TransitionResult: ...
```

```python
@dataclass
class ListPage:
    items: list[dict]
    next_cursor: str | None      # None means no further pages

@dataclass
class TransitionResult:
    record: dict | None
    error: str | None            # None on success; otherwise a stable code:
                                 # "not_found" | "invalid_transition" | "guard_failed"
```

Rationale for the two changes from the old interface:

- **`ListPage` with a cursor, not `(items, total)` with an offset.** Specora
  apps must support arbitrary size. `SELECT COUNT(*)` is a full scan on every
  request and `OFFSET n` discards n rows server-side, so both degrade linearly
  with table size on the hottest endpoint. Keyset pagination is O(log n)
  regardless of depth, needs one query instead of two, and removes the window
  where `count` and `items` disagree.

- **`TransitionResult` with an error code, not `dict | None`.** The old return
  collapsed "no such record", "illegal transition", and "guard failed" into
  `None`, so every failure surfaced as an indistinguishable 422. Callers need
  to map them to 404 / 409 / 422 separately.

Every adapter implements the full interface with identical semantics. `filters`
in particular must work in **both** adapters — the Postgres one previously
accepted and silently ignored it while the memory one honoured it, so a
filtered query returned unfiltered rows in production.

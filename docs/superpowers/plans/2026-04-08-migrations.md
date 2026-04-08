# Database Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate versioned ALTER TABLE migrations from contract diffs instead of overwriting schema.sql. Auto-apply on Docker startup. Postgres-only.

**Architecture:** IR caching (save compiled DomainIR to `.forge/ir_cache/`), schema diff engine (compare old vs new EntityIR), SQL writer (SchemaChange → ALTER TABLE), migration generator (BaseGenerator that produces versioned .sql files), startup runner (auto-apply pending migrations before serving).

**Tech Stack:** Python 3.10+, existing Pydantic IR models, existing Postgres type mapping, asyncpg for migration runner.

**Spec:** `docs/superpowers/specs/2026-04-08-migrations-design.md`
**Issue:** syndicalt/specora-core#12

---

## File Map

| File | Responsibility |
|------|---------------|
| `forge/targets/migrations/__init__.py` | Package init |
| `forge/targets/migrations/ir_cache.py` | Save/load DomainIR snapshots to `.forge/ir_cache/` |
| `forge/targets/migrations/differ.py` | Compare old vs new EntityIR → list of SchemaChange |
| `forge/targets/migrations/sql_writer.py` | SchemaChange → Postgres SQL statements |
| `forge/targets/migrations/generator.py` | MigrationGenerator — orchestrates cache + diff + SQL |
| `tests/test_targets/test_migrations.py` | Tests for IR cache, differ, SQL writer, generator |

---

### Task 1: IR Cache

**Files:**
- Create: `forge/targets/migrations/__init__.py`
- Create: `forge/targets/migrations/ir_cache.py`
- Create: `tests/test_targets/test_migrations.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_targets/test_migrations.py
"""Tests for database migration system."""
import json
from pathlib import Path

import pytest

from forge.ir.model import DomainIR, EntityIR, FieldIR


@pytest.fixture
def sample_ir() -> DomainIR:
    return DomainIR(
        domain="test",
        entities=[
            EntityIR(
                fqn="entity/test/task",
                name="task",
                domain="test",
                table_name="tasks",
                fields=[
                    FieldIR(name="title", type="string", required=True),
                    FieldIR(name="priority", type="string", enum_values=["high", "low"]),
                    FieldIR(name="id", type="uuid", computed="uuid"),
                    FieldIR(name="created_at", type="datetime", computed="now"),
                    FieldIR(name="updated_at", type="datetime", computed="now_on_update"),
                ],
            ),
        ],
    )


class TestIRCache:

    def test_save_and_load(self, tmp_path: Path, sample_ir: DomainIR) -> None:
        from forge.targets.migrations.ir_cache import save_ir_cache, load_ir_cache

        cache_path = tmp_path / "ir_cache"
        save_ir_cache(sample_ir, cache_path)

        loaded = load_ir_cache(cache_path)
        assert loaded is not None
        assert loaded.domain == "test"
        assert len(loaded.entities) == 1
        assert loaded.entities[0].name == "task"
        assert len(loaded.entities[0].fields) == 5

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        from forge.targets.migrations.ir_cache import load_ir_cache

        loaded = load_ir_cache(tmp_path / "nonexistent")
        assert loaded is None

    def test_round_trips_field_details(self, tmp_path: Path, sample_ir: DomainIR) -> None:
        from forge.targets.migrations.ir_cache import save_ir_cache, load_ir_cache

        cache_path = tmp_path / "ir_cache"
        save_ir_cache(sample_ir, cache_path)
        loaded = load_ir_cache(cache_path)

        task = loaded.entities[0]
        title = next(f for f in task.fields if f.name == "title")
        assert title.required is True
        assert title.type == "string"

        priority = next(f for f in task.fields if f.name == "priority")
        assert priority.enum_values == ["high", "low"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_targets/test_migrations.py -v`

- [ ] **Step 3: Implement IR cache**

```python
# forge/targets/migrations/__init__.py
# (empty)
```

```python
# forge/targets/migrations/ir_cache.py
"""Save and load DomainIR snapshots for migration diffing."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from forge.ir.model import DomainIR

logger = logging.getLogger(__name__)

CACHE_FILENAME = "domain_ir.json"


def save_ir_cache(ir: DomainIR, cache_dir: Path) -> None:
    """Save a DomainIR snapshot to the cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / CACHE_FILENAME
    data = ir.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info("Saved IR cache to %s", path)


def load_ir_cache(cache_dir: Path) -> Optional[DomainIR]:
    """Load a DomainIR snapshot from the cache directory.

    Returns None if no cache exists.
    """
    path = cache_dir / CACHE_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DomainIR(**data)
    except Exception as e:
        logger.warning("Failed to load IR cache: %s", e)
        return None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_targets/test_migrations.py::TestIRCache -v`

- [ ] **Step 5: Commit**

```bash
git add forge/targets/migrations/ tests/test_targets/test_migrations.py
git commit -m "feat(#12/T1): IR cache — save/load DomainIR snapshots for migration diffing"
```

---

### Task 2: Schema Differ

**Files:**
- Create: `forge/targets/migrations/differ.py`
- Modify: `tests/test_targets/test_migrations.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_targets/test_migrations.py`:

```python
from forge.ir.model import ReferenceIR, StateMachineIR, StateIR


class TestSchemaDiffer:

    def test_no_changes(self) -> None:
        from forge.targets.migrations.differ import diff_entities, SchemaChange

        old = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
        ])]
        new = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
        ])]
        changes = diff_entities(old, new)
        assert changes == []

    def test_new_entity(self) -> None:
        from forge.targets.migrations.differ import diff_entities

        old = []
        new = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
        ])]
        changes = diff_entities(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "create_table"
        assert changes[0].table_name == "tasks"

    def test_removed_entity(self) -> None:
        from forge.targets.migrations.differ import diff_entities

        old = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[])]
        new = []
        changes = diff_entities(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "drop_table"
        assert changes[0].destructive is True

    def test_field_added(self) -> None:
        from forge.targets.migrations.differ import diff_entities

        old = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
        ])]
        new = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
            FieldIR(name="priority", type="string"),
        ])]
        changes = diff_entities(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "add_column"
        assert changes[0].field_name == "priority"

    def test_field_removed(self) -> None:
        from forge.targets.migrations.differ import diff_entities

        old = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
            FieldIR(name="priority", type="string"),
        ])]
        new = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
        ])]
        changes = diff_entities(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "drop_column"
        assert changes[0].destructive is True

    def test_field_type_changed(self) -> None:
        from forge.targets.migrations.differ import diff_entities

        old = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="count", type="string"),
        ])]
        new = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="count", type="integer"),
        ])]
        changes = diff_entities(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "alter_type"

    def test_field_became_required(self) -> None:
        from forge.targets.migrations.differ import diff_entities

        old = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=False),
        ])]
        new = [EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
        ])]
        changes = diff_entities(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "set_not_null"
```

- [ ] **Step 2: Implement differ**

```python
# forge/targets/migrations/differ.py
"""Compare old vs new EntityIR to detect schema changes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from forge.ir.model import EntityIR, FieldIR


@dataclass
class SchemaChange:
    """A single schema change detected between IR versions."""
    change_type: str          # create_table, drop_table, add_column, drop_column, alter_type, set_not_null, drop_not_null, set_default, drop_default, add_index
    table_name: str
    field_name: str = ""
    old_value: Any = None
    new_value: Any = None
    entity: Optional[EntityIR] = field(default=None, repr=False)
    field_ir: Optional[FieldIR] = field(default=None, repr=False)
    destructive: bool = False


def diff_entities(
    old_entities: list[EntityIR],
    new_entities: list[EntityIR],
) -> list[SchemaChange]:
    """Compare old and new entity lists, return schema changes.

    Detects: new tables, dropped tables, added columns, dropped columns,
    type changes, nullability changes, default changes.
    """
    changes: list[SchemaChange] = []

    old_by_fqn = {e.fqn: e for e in old_entities}
    new_by_fqn = {e.fqn: e for e in new_entities}

    # New entities (tables to create)
    for fqn, entity in new_by_fqn.items():
        if fqn not in old_by_fqn:
            changes.append(SchemaChange(
                change_type="create_table",
                table_name=entity.table_name,
                entity=entity,
            ))

    # Removed entities (tables to drop)
    for fqn, entity in old_by_fqn.items():
        if fqn not in new_by_fqn:
            changes.append(SchemaChange(
                change_type="drop_table",
                table_name=entity.table_name,
                destructive=True,
            ))

    # Changed entities (columns to alter)
    for fqn in old_by_fqn:
        if fqn in new_by_fqn:
            changes.extend(_diff_entity_fields(old_by_fqn[fqn], new_by_fqn[fqn]))

    return changes


def _diff_entity_fields(old: EntityIR, new: EntityIR) -> list[SchemaChange]:
    """Compare fields between two versions of the same entity."""
    changes: list[SchemaChange] = []
    table = new.table_name

    old_fields = {f.name: f for f in old.fields}
    new_fields = {f.name: f for f in new.fields}

    # Added fields
    for name, f in new_fields.items():
        if name not in old_fields:
            changes.append(SchemaChange(
                change_type="add_column",
                table_name=table,
                field_name=name,
                field_ir=f,
            ))

    # Removed fields
    for name, f in old_fields.items():
        if name not in new_fields:
            changes.append(SchemaChange(
                change_type="drop_column",
                table_name=table,
                field_name=name,
                destructive=True,
            ))

    # Modified fields
    for name in old_fields:
        if name in new_fields:
            old_f = old_fields[name]
            new_f = new_fields[name]

            # Type change
            if old_f.type != new_f.type:
                changes.append(SchemaChange(
                    change_type="alter_type",
                    table_name=table,
                    field_name=name,
                    old_value=old_f.type,
                    new_value=new_f.type,
                    field_ir=new_f,
                ))

            # Nullability change
            if not old_f.required and new_f.required:
                changes.append(SchemaChange(
                    change_type="set_not_null",
                    table_name=table,
                    field_name=name,
                ))
            elif old_f.required and not new_f.required:
                changes.append(SchemaChange(
                    change_type="drop_not_null",
                    table_name=table,
                    field_name=name,
                ))

            # Default change
            if old_f.default != new_f.default:
                if new_f.default is not None and new_f.default != "":
                    changes.append(SchemaChange(
                        change_type="set_default",
                        table_name=table,
                        field_name=name,
                        new_value=new_f.default,
                    ))
                elif old_f.default is not None and old_f.default != "":
                    changes.append(SchemaChange(
                        change_type="drop_default",
                        table_name=table,
                        field_name=name,
                    ))

    return changes
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_targets/test_migrations.py::TestSchemaDiffer -v`

- [ ] **Step 4: Commit**

```bash
git add forge/targets/migrations/differ.py tests/test_targets/test_migrations.py
git commit -m "feat(#12/T2): schema differ — detect changes between EntityIR versions"
```

---

### Task 3: SQL Writer

**Files:**
- Create: `forge/targets/migrations/sql_writer.py`
- Modify: `tests/test_targets/test_migrations.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_targets/test_migrations.py`:

```python
class TestSQLWriter:

    def test_add_column(self) -> None:
        from forge.targets.migrations.differ import SchemaChange
        from forge.targets.migrations.sql_writer import schema_change_to_sql

        change = SchemaChange(
            change_type="add_column",
            table_name="tasks",
            field_name="priority",
            field_ir=FieldIR(name="priority", type="string"),
        )
        sql = schema_change_to_sql(change)
        assert "ALTER TABLE tasks ADD COLUMN priority TEXT" in sql

    def test_add_required_column(self) -> None:
        from forge.targets.migrations.differ import SchemaChange
        from forge.targets.migrations.sql_writer import schema_change_to_sql

        change = SchemaChange(
            change_type="add_column",
            table_name="tasks",
            field_name="title",
            field_ir=FieldIR(name="title", type="string", required=True),
        )
        sql = schema_change_to_sql(change)
        assert "NOT NULL" in sql

    def test_drop_column_has_warning(self) -> None:
        from forge.targets.migrations.differ import SchemaChange
        from forge.targets.migrations.sql_writer import schema_change_to_sql

        change = SchemaChange(
            change_type="drop_column",
            table_name="tasks",
            field_name="old_field",
            destructive=True,
        )
        sql = schema_change_to_sql(change)
        assert "WARNING: DESTRUCTIVE" in sql
        assert "DROP COLUMN" in sql

    def test_alter_type(self) -> None:
        from forge.targets.migrations.differ import SchemaChange
        from forge.targets.migrations.sql_writer import schema_change_to_sql

        change = SchemaChange(
            change_type="alter_type",
            table_name="tasks",
            field_name="count",
            old_value="string",
            new_value="integer",
            field_ir=FieldIR(name="count", type="integer"),
        )
        sql = schema_change_to_sql(change)
        assert "ALTER COLUMN count TYPE INTEGER" in sql

    def test_set_not_null(self) -> None:
        from forge.targets.migrations.differ import SchemaChange
        from forge.targets.migrations.sql_writer import schema_change_to_sql

        change = SchemaChange(
            change_type="set_not_null",
            table_name="tasks",
            field_name="title",
        )
        sql = schema_change_to_sql(change)
        assert "SET NOT NULL" in sql

    def test_create_table(self) -> None:
        from forge.targets.migrations.differ import SchemaChange
        from forge.targets.migrations.sql_writer import schema_change_to_sql

        entity = EntityIR(fqn="entity/t/task", name="task", domain="t", table_name="tasks", fields=[
            FieldIR(name="title", type="string", required=True),
            FieldIR(name="id", type="uuid", computed="uuid"),
        ])
        change = SchemaChange(change_type="create_table", table_name="tasks", entity=entity)
        sql = schema_change_to_sql(change)
        assert "CREATE TABLE" in sql
        assert "tasks" in sql
```

- [ ] **Step 2: Implement SQL writer**

```python
# forge/targets/migrations/sql_writer.py
"""Convert SchemaChange objects into Postgres SQL statements."""
from __future__ import annotations

from forge.ir.model import EntityIR, FieldIR
from forge.targets.migrations.differ import SchemaChange
from forge.targets.postgres.gen_ddl import PG_TYPE_MAP, AUTO_INDEX_FIELDS


def schema_change_to_sql(change: SchemaChange) -> str:
    """Convert a single SchemaChange to a SQL statement."""
    handlers = {
        "create_table": _create_table,
        "drop_table": _drop_table,
        "add_column": _add_column,
        "drop_column": _drop_column,
        "alter_type": _alter_type,
        "set_not_null": _set_not_null,
        "drop_not_null": _drop_not_null,
        "set_default": _set_default,
        "drop_default": _drop_default,
        "add_index": _add_index,
    }
    handler = handlers.get(change.change_type)
    if handler:
        return handler(change)
    return f"-- Unknown change type: {change.change_type}"


def changes_to_sql(changes: list[SchemaChange]) -> str:
    """Convert a list of SchemaChanges to a complete migration SQL string."""
    if not changes:
        return "-- No schema changes detected.\n"

    statements = []
    for change in changes:
        sql = schema_change_to_sql(change)
        if sql:
            statements.append(sql)

    return "\n\n".join(statements) + "\n"


def _create_table(change: SchemaChange) -> str:
    entity = change.entity
    if not entity:
        return f"-- Cannot create table {change.table_name}: no entity data"

    lines = [f"CREATE TABLE IF NOT EXISTS {entity.table_name} ("]
    columns = []
    for field in entity.fields:
        columns.append(_column_def(field))
    columns.append("    data JSONB DEFAULT '{}'::jsonb")
    lines.append(",\n".join(columns))
    lines.append(");")

    # Indexes
    for field in entity.fields:
        idx = _maybe_index(entity.table_name, field)
        if idx:
            lines.append(idx)

    return "\n".join(lines)


def _drop_table(change: SchemaChange) -> str:
    return (
        f"-- WARNING: DESTRUCTIVE — dropping table {change.table_name}\n"
        f"DROP TABLE IF EXISTS {change.table_name} CASCADE;"
    )


def _add_column(change: SchemaChange) -> str:
    field = change.field_ir
    if not field:
        return f"ALTER TABLE {change.table_name} ADD COLUMN {change.field_name} TEXT;"

    pg_type = PG_TYPE_MAP.get(field.type, "TEXT")
    parts = [f"ALTER TABLE {change.table_name} ADD COLUMN {field.name} {pg_type}"]

    if field.required:
        parts.append("NOT NULL")
    if field.default is not None and field.default != "":
        parts.append(f"DEFAULT {_format_default(field.default)}")

    sql = " ".join(parts) + ";"

    # Add index if needed
    idx = _maybe_index(change.table_name, field)
    if idx:
        sql += "\n" + idx

    return sql


def _drop_column(change: SchemaChange) -> str:
    return (
        f"-- WARNING: DESTRUCTIVE — dropping column {change.field_name}\n"
        f"ALTER TABLE {change.table_name} DROP COLUMN IF EXISTS {change.field_name};"
    )


def _alter_type(change: SchemaChange) -> str:
    new_type = PG_TYPE_MAP.get(change.new_value, "TEXT")
    return (
        f"ALTER TABLE {change.table_name} "
        f"ALTER COLUMN {change.field_name} TYPE {new_type} "
        f"USING {change.field_name}::{new_type};"
    )


def _set_not_null(change: SchemaChange) -> str:
    return f"ALTER TABLE {change.table_name} ALTER COLUMN {change.field_name} SET NOT NULL;"


def _drop_not_null(change: SchemaChange) -> str:
    return f"ALTER TABLE {change.table_name} ALTER COLUMN {change.field_name} DROP NOT NULL;"


def _set_default(change: SchemaChange) -> str:
    return f"ALTER TABLE {change.table_name} ALTER COLUMN {change.field_name} SET DEFAULT {_format_default(change.new_value)};"


def _drop_default(change: SchemaChange) -> str:
    return f"ALTER TABLE {change.table_name} ALTER COLUMN {change.field_name} DROP DEFAULT;"


def _add_index(change: SchemaChange) -> str:
    return f"CREATE INDEX IF NOT EXISTS idx_{change.table_name}_{change.field_name} ON {change.table_name} ({change.field_name});"


def _column_def(field: FieldIR) -> str:
    """Generate a column definition (reuses gen_ddl logic)."""
    pg_type = PG_TYPE_MAP.get(field.type, "TEXT")

    if field.name == "id" and field.type == "uuid":
        return "    id UUID PRIMARY KEY DEFAULT gen_random_uuid()"
    if field.name == "number":
        return "    number TEXT UNIQUE"

    parts = [f"    {field.name}", pg_type]
    if field.required:
        parts.append("NOT NULL")
    if field.default is not None and field.default != "":
        parts.append(f"DEFAULT {_format_default(field.default)}")
    return " ".join(parts)


def _format_default(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f"'{value}'"
    return f"'{value}'"


def _maybe_index(table_name: str, field: FieldIR) -> str:
    if field.name == "id":
        return ""
    if field.name in AUTO_INDEX_FIELDS or field.reference:
        return f"CREATE INDEX IF NOT EXISTS idx_{table_name}_{field.name} ON {table_name} ({field.name});"
    return ""
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_targets/test_migrations.py::TestSQLWriter -v`

- [ ] **Step 4: Commit**

```bash
git add forge/targets/migrations/sql_writer.py tests/test_targets/test_migrations.py
git commit -m "feat(#12/T3): SQL writer — SchemaChange to Postgres ALTER TABLE statements"
```

---

### Task 4: Migration Generator

**Files:**
- Create: `forge/targets/migrations/generator.py`
- Modify: `forge/cli/main.py` — register `migrations` target
- Modify: `tests/test_targets/test_migrations.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_targets/test_migrations.py`:

```python
class TestMigrationGenerator:

    def test_first_generation_creates_initial(self, tmp_path: Path, sample_ir: DomainIR) -> None:
        from forge.targets.migrations.generator import MigrationGenerator

        gen = MigrationGenerator(ir_cache_path=tmp_path / "cache", migrations_dir=tmp_path / "migrations")
        files = gen.generate(sample_ir)

        assert len(files) == 1
        assert files[0].path == "database/migrations/001_initial.sql"
        assert "CREATE TABLE" in files[0].content

    def test_second_generation_with_change(self, tmp_path: Path) -> None:
        from forge.targets.migrations.generator import MigrationGenerator

        gen = MigrationGenerator(ir_cache_path=tmp_path / "cache", migrations_dir=tmp_path / "migrations")

        # First generation
        ir1 = DomainIR(domain="test", entities=[
            EntityIR(fqn="entity/test/task", name="task", domain="test", table_name="tasks", fields=[
                FieldIR(name="title", type="string", required=True),
                FieldIR(name="id", type="uuid", computed="uuid"),
            ]),
        ])
        files1 = gen.generate(ir1)
        assert len(files1) == 1
        # Write to disk so next generation finds them
        for f in files1:
            p = tmp_path / "migrations" / Path(f.path).name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f.content)

        # Second generation — add a field
        ir2 = DomainIR(domain="test", entities=[
            EntityIR(fqn="entity/test/task", name="task", domain="test", table_name="tasks", fields=[
                FieldIR(name="title", type="string", required=True),
                FieldIR(name="priority", type="string"),
                FieldIR(name="id", type="uuid", computed="uuid"),
            ]),
        ])
        files2 = gen.generate(ir2)
        assert len(files2) == 1
        assert "002_" in files2[0].path
        assert "ADD COLUMN priority" in files2[0].content

    def test_no_changes_produces_no_migration(self, tmp_path: Path) -> None:
        from forge.targets.migrations.generator import MigrationGenerator

        gen = MigrationGenerator(ir_cache_path=tmp_path / "cache", migrations_dir=tmp_path / "migrations")

        ir = DomainIR(domain="test", entities=[
            EntityIR(fqn="entity/test/task", name="task", domain="test", table_name="tasks", fields=[
                FieldIR(name="title", type="string", required=True),
            ]),
        ])

        # First gen
        files1 = gen.generate(ir)
        for f in files1:
            p = tmp_path / "migrations" / Path(f.path).name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f.content)

        # Second gen — same IR
        files2 = gen.generate(ir)
        assert len(files2) == 0
```

- [ ] **Step 2: Implement generator**

```python
# forge/targets/migrations/generator.py
"""Migration generator — orchestrates IR caching, diffing, and SQL generation."""
from __future__ import annotations

import re
from pathlib import Path

from forge.ir.model import DomainIR
from forge.targets.base import BaseGenerator, GeneratedFile, provenance_header
from forge.targets.migrations.differ import diff_entities
from forge.targets.migrations.ir_cache import load_ir_cache, save_ir_cache
from forge.targets.migrations.sql_writer import changes_to_sql, schema_change_to_sql


class MigrationGenerator(BaseGenerator):
    """Generates versioned migration files from IR diffs."""

    def __init__(
        self,
        ir_cache_path: Path = Path(".forge/ir_cache"),
        migrations_dir: Path = Path("runtime/database/migrations"),
    ) -> None:
        self._cache_path = ir_cache_path
        self._migrations_dir = migrations_dir

    def name(self) -> str:
        return "migrations"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        """Generate migration files by diffing against cached IR.

        First run (no cache): generates 001_initial.sql with CREATE TABLE.
        Subsequent runs: generates numbered ALTER TABLE migration.
        No changes: returns empty list.
        Always saves the new IR to cache.
        """
        previous_ir = load_ir_cache(self._cache_path)
        next_version = self._next_version()

        if previous_ir is None:
            # First generation — full initial schema
            save_ir_cache(ir, self._cache_path)
            return self._generate_initial(ir, next_version)

        # Diff old vs new
        changes = diff_entities(previous_ir.entities, ir.entities)

        # Save new IR to cache
        save_ir_cache(ir, self._cache_path)

        if not changes:
            return []

        # Generate migration
        sql = changes_to_sql(changes)
        description = self._describe_changes(changes)
        version_str = f"{next_version:03d}"
        slug = re.sub(r"[^a-z0-9]+", "_", description.lower())[:40].strip("_")
        filename = f"{version_str}_{slug}.sql"

        header = provenance_header("sql", f"domain/{ir.domain}", f"Migration: {description}")

        return [GeneratedFile(
            path=f"database/migrations/{filename}",
            content=header + sql,
            provenance=f"domain/{ir.domain}",
        )]

    def _generate_initial(self, ir: DomainIR, version: int) -> list[GeneratedFile]:
        """Generate the initial migration (CREATE TABLE for all entities)."""
        from forge.targets.migrations.sql_writer import _create_table

        statements = ['CREATE EXTENSION IF NOT EXISTS "pgcrypto";\n']
        for entity in ir.entities:
            from forge.targets.migrations.differ import SchemaChange
            change = SchemaChange(change_type="create_table", table_name=entity.table_name, entity=entity)
            statements.append(schema_change_to_sql(change))

        header = provenance_header("sql", f"domain/{ir.domain}", "Initial schema")
        content = header + "\n\n".join(statements) + "\n"

        return [GeneratedFile(
            path=f"database/migrations/{version:03d}_initial.sql",
            content=content,
            provenance=f"domain/{ir.domain}",
        )]

    def _next_version(self) -> int:
        """Determine the next migration version number."""
        if not self._migrations_dir.exists():
            return 1
        existing = sorted(self._migrations_dir.glob("*.sql"))
        if not existing:
            return 1
        last = existing[-1].name
        match = re.match(r"(\d+)_", last)
        if match:
            return int(match.group(1)) + 1
        return len(existing) + 1

    def _describe_changes(self, changes) -> str:
        """Generate a human-readable description of changes."""
        if len(changes) == 1:
            c = changes[0]
            if c.change_type == "create_table":
                return f"add {c.table_name} table"
            if c.change_type == "drop_table":
                return f"drop {c.table_name} table"
            if c.change_type == "add_column":
                return f"add {c.field_name} to {c.table_name}"
            if c.change_type == "drop_column":
                return f"drop {c.field_name} from {c.table_name}"
            if c.change_type == "alter_type":
                return f"change {c.field_name} type in {c.table_name}"
            if c.change_type in ("set_not_null", "drop_not_null"):
                return f"change {c.field_name} nullability in {c.table_name}"
        return f"{len(changes)} schema changes"
```

- [ ] **Step 3: Register in CLI**

Read `forge/cli/main.py` and add to the `_get_generators` registry:

```python
from forge.targets.migrations.generator import MigrationGenerator
```

Add to registry dict:
```python
"migrations": MigrationGenerator,
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_targets/test_migrations.py -v`
Run: `python -m pytest tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add forge/targets/migrations/generator.py forge/cli/main.py tests/test_targets/test_migrations.py
git commit -m "feat(#12/T4): migration generator — versioned SQL from IR diffs + CLI registration"
```

---

### Task 5: Migration Runner in Generated App

**Files:**
- Modify: `forge/targets/fastapi_prod/gen_app.py` — add startup migration runner

- [ ] **Step 1: Update app generator**

Read `forge/targets/fastapi_prod/gen_app.py`. Add a migration runner to the generated `app.py` startup:

After the existing `app = FastAPI(...)` line and before the route includes, add:

```python
        "",
        "# ── Migration runner ─────────────────────────────────────────────",
        "",
        "import glob",
        "",
        "@app.on_event('startup')",
        "async def run_migrations():",
        '    """Apply pending database migrations on startup."""',
        "    if DATABASE_BACKEND != 'postgres':",
        "        return",
        "    try:",
        "        import asyncpg",
        "        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)",
        "        async with pool.acquire() as conn:",
        "            # Create migrations table",
        '            await conn.execute("""',
        "                CREATE TABLE IF NOT EXISTS _migrations (",
        "                    name TEXT PRIMARY KEY,",
        "                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "                )",
        '            """)',
        "            # Get applied migrations",
        '            rows = await conn.fetch("SELECT name FROM _migrations")',
        "            applied = {r['name'] for r in rows}",
        "            # Find and apply pending",
        "            migration_files = sorted(glob.glob('database/migrations/*.sql'))",
        "            for mf in migration_files:",
        "                name = mf.split('/')[-1].split('\\\\')[-1]",
        "                if name not in applied:",
        "                    sql = open(mf).read()",
        "                    await conn.execute(sql)",
        '                    await conn.execute("INSERT INTO _migrations (name) VALUES ($1)", name)',
        f"                    print(f'Applied migration: {{name}}')",
        "        await pool.close()",
        "    except Exception as e:",
        f"        print(f'Migration error: {{e}}')",
        "",
```

Also add these imports near the top of the generated app:
```python
"from backend.config import DATABASE_URL, DATABASE_BACKEND",
```

Modify the existing config import line to include `DATABASE_URL` and `DATABASE_BACKEND`.

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -q`

- [ ] **Step 3: Verify end-to-end**

```bash
python -m forge.cli.main forge generate domains/task_manager --target fastapi-prod --target postgres --target migrations --output runtime/
ls runtime/database/migrations/
cat runtime/database/migrations/001_initial.sql | head -20
```

- [ ] **Step 4: Commit**

```bash
git add forge/targets/fastapi_prod/gen_app.py
git commit -m "feat(#12/T5): migration runner in generated app — auto-apply on startup"
```

---

## Verification Checklist

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] First generation produces `001_initial.sql`
- [ ] Adding a field and regenerating produces `002_add_xxx.sql` with `ALTER TABLE ADD COLUMN`
- [ ] Removing a field produces `-- WARNING: DESTRUCTIVE` + `DROP COLUMN`
- [ ] No changes produces no migration file
- [ ] Generated `app.py` includes migration runner
- [ ] `runtime/database/migrations/` has versioned files
- [ ] `schema.sql` is still generated (for fresh deploys)

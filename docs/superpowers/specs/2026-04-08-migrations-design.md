# Database Migrations — Design Spec

**Date:** 2026-04-08
**Status:** Approved
**Issue:** syndicalt/specora-core#12

## Purpose

When contracts change, generate versioned `ALTER TABLE` migration files instead of overwriting `schema.sql`. Migrations run automatically on Docker startup. First generation produces `001_initial.sql`. Subsequent generations compare the previous IR snapshot against the current IR and emit only the differences.

## Architecture

### Generation Flow

```
forge generate (with migrations)
  1. Load previous IR from .forge/ir_cache/domain_ir.json
  2. Compile current contracts → new DomainIR
  3. If no previous IR → generate 001_initial.sql (CREATE TABLE)
  4. If previous IR exists → diff old vs new EntityIR per entity
  5. Generate versioned migration file (002_xxx.sql, 003_xxx.sql, etc.)
  6. Save new IR snapshot to .forge/ir_cache/domain_ir.json
  7. Always regenerate schema.sql (for fresh deploys)
```

### IR Cache

Location: `.forge/ir_cache/domain_ir.json`

Serialized DomainIR (entities only — workflows/routes/pages don't affect schema). Saved after every successful generation. Used as the "before" snapshot for the next diff.

### Schema Diff Engine

Compares previous `EntityIR` against current `EntityIR` for each entity:

| Change Detected | Migration SQL |
|----------------|--------------|
| New entity (not in previous IR) | `CREATE TABLE IF NOT EXISTS {table} (...)` |
| Entity removed (in previous, not in current) | `-- WARNING: DESTRUCTIVE` + `DROP TABLE IF EXISTS {table}` |
| Field added | `ALTER TABLE {table} ADD COLUMN {name} {type} [NOT NULL] [DEFAULT ...]` |
| Field removed | `-- WARNING: DESTRUCTIVE` + `ALTER TABLE {table} DROP COLUMN IF EXISTS {name}` |
| Field type changed | `ALTER TABLE {table} ALTER COLUMN {name} TYPE {new_type} USING {name}::{new_type}` |
| Field became required | `ALTER TABLE {table} ALTER COLUMN {name} SET NOT NULL` |
| Field became optional | `ALTER TABLE {table} ALTER COLUMN {name} DROP NOT NULL` |
| Default value added | `ALTER TABLE {table} ALTER COLUMN {name} SET DEFAULT {value}` |
| Default value removed | `ALTER TABLE {table} ALTER COLUMN {name} DROP DEFAULT` |
| State machine added to entity | `ALTER TABLE {table} ADD COLUMN state TEXT DEFAULT '{initial}'` + index |
| New reference field | `ALTER TABLE {table} ADD COLUMN {name} UUID` + index |
| New index needed | `CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table} ({col})` |

### Generated Files

```
runtime/database/
├── schema.sql                    ← Full DDL (always regenerated, for fresh deploys)
└── migrations/
    ├── 001_initial.sql           ← First generation (full CREATE TABLE)
    ├── 002_add_review_entity.sql ← Second generation (new entity)
    └── 003_make_resolution_required.sql  ← Healer fix
```

Each migration file includes:
- Provenance header (source contracts, timestamp)
- The SQL statements
- Destructive warnings where applicable

### Migration Tracking Table

```sql
CREATE TABLE IF NOT EXISTS _migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Auto-Run on Startup

The generated `app.py` includes a startup event that:
1. Connects to the database
2. Creates `_migrations` table if it doesn't exist
3. Reads applied migrations from `_migrations`
4. Scans `database/migrations/` for pending files (sorted by name)
5. Applies each pending migration in a transaction
6. Records the migration name in `_migrations`
7. If any migration fails, the transaction rolls back and the app does NOT start (fail-fast)

### Integration with Existing Generators

The migration system is a new generator target: `migrations`

```python
spc forge generate domains/helpdesk --target fastapi-prod --target postgres --target migrations --target docker
```

Or via Python API:
```python
from forge.targets.migrations.generator import MigrationGenerator
gen = MigrationGenerator(ir_cache_path=Path(".forge/ir_cache"))
files = gen.generate(ir)  # Returns list of GeneratedFile
```

The `fastapi-prod` app generator is updated to include the migration runner in the startup event.

## Project Layout

```
forge/targets/migrations/
├── __init__.py
├── generator.py         # MigrationGenerator — orchestrates IR caching + diffing
├── differ.py            # Compare old vs new EntityIR → list of SchemaChange
├── sql_writer.py        # SchemaChange → SQL statements
└── ir_cache.py          # Save/load DomainIR snapshots
```

## Dependencies

- Existing: `forge.ir.model` (EntityIR, FieldIR, DomainIR)
- Existing: `forge.targets.postgres.gen_ddl` (type mapping, reused)
- Existing: `forge.targets.base` (BaseGenerator, GeneratedFile)
- New: migration runner code in generated `app.py` startup

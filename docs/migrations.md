# Migrations

Specora Core generates versioned, incremental SQL migrations automatically. When you change a contract and regenerate, the migration generator compares the new IR against a cached snapshot of the previous IR, detects schema differences, and emits numbered `.sql` files with the appropriate `ALTER TABLE` statements. On Docker startup, the generated app runs all pending migrations automatically.

---

## How It Works

```
Contracts ──> Compiler ──> DomainIR (new)
                              |
                              |  diff against
                              v
                         IR Cache (old)
                              |
                              v
                        SchemaChange[]
                              |
                              v
                     SQL Migration File
                              |
                              v
                   database/migrations/002_add_severity_to_incidents.sql
```

Three components work together:

1. **IR Cache** (`forge/targets/migrations/ir_cache.py`) -- Saves and loads `DomainIR` snapshots as JSON in `.forge/ir_cache/domain_ir.json`.
2. **Differ** (`forge/targets/migrations/differ.py`) -- Compares old and new `EntityIR` lists, returns a list of `SchemaChange` objects.
3. **SQL Writer** (`forge/targets/migrations/sql_writer.py`) -- Converts `SchemaChange` objects into PostgreSQL SQL statements.

The `MigrationGenerator` (`forge/targets/migrations/generator.py`) orchestrates all three.

---

## First Generation vs. Subsequent Generations

### First Generation (No Cache)

When no IR cache exists (fresh project), the generator:

1. Creates `001_initial.sql` with `CREATE TABLE` for every entity
2. Includes `CREATE EXTENSION IF NOT EXISTS "pgcrypto"` for UUID generation
3. Saves the current IR to the cache

```sql
-- 001_initial.sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    number TEXT UNIQUE,
    subject TEXT NOT NULL,
    priority TEXT NOT NULL,
    customer_id UUID NOT NULL,
    data JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_tickets_customer_id ON tickets (customer_id);

CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    number TEXT UNIQUE,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    data JSONB DEFAULT '{}'::jsonb
);
```

### Subsequent Generations (Cache Exists)

When a cache exists, the generator:

1. Loads the previous IR from `.forge/ir_cache/domain_ir.json`
2. Diffs old entities against new entities
3. If no changes: returns an empty list (no migration file)
4. If changes: generates a numbered migration file with `ALTER TABLE` statements
5. Saves the new IR to the cache (overwriting the old snapshot)

```sql
-- 002_add_severity_to_incidents.sql
ALTER TABLE incidents ADD COLUMN severity TEXT NOT NULL DEFAULT 'medium';
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents (severity);
```

---

## Detected Change Types

The differ detects 10 types of schema changes:

| Change Type | SQL | Destructive |
|-------------|-----|-------------|
| `create_table` | `CREATE TABLE ...` | No |
| `drop_table` | `DROP TABLE IF EXISTS ... CASCADE` | **Yes** |
| `add_column` | `ALTER TABLE ... ADD COLUMN ...` | No |
| `drop_column` | `ALTER TABLE ... DROP COLUMN IF EXISTS ...` | **Yes** |
| `alter_type` | `ALTER TABLE ... ALTER COLUMN ... TYPE ... USING ...::type` | No |
| `set_not_null` | `ALTER TABLE ... ALTER COLUMN ... SET NOT NULL` | No |
| `drop_not_null` | `ALTER TABLE ... ALTER COLUMN ... DROP NOT NULL` | No |
| `set_default` | `ALTER TABLE ... ALTER COLUMN ... SET DEFAULT ...` | No |
| `drop_default` | `ALTER TABLE ... ALTER COLUMN ... DROP DEFAULT` | No |
| `add_index` | `CREATE INDEX IF NOT EXISTS ...` | No |

### Destructive Warnings

Destructive operations (`drop_table`, `drop_column`) include a SQL comment warning:

```sql
-- WARNING: DESTRUCTIVE -- dropping column severity
ALTER TABLE incidents DROP COLUMN IF EXISTS severity;
```

These are generated when you remove an entity from your contracts or remove a field from an entity. Review destructive migrations before applying.

---

## Migration Versioning

Files are named `{NNN}_{description}.sql`:

- `001_initial.sql` -- first generation
- `002_add_severity_to_incidents.sql` -- adding a field
- `003_drop_old_field_from_tickets.sql` -- removing a field
- `004_3_schema_changes.sql` -- multiple changes

The version number is determined by scanning the `database/migrations/` directory for existing `.sql` files and incrementing from the last one.

The description is auto-generated from the changes:
- Single `create_table`: `add {table_name} table`
- Single `drop_table`: `drop {table_name} table`
- Single `add_column`: `add {field_name} to {table_name}`
- Single `drop_column`: `drop {field_name} from {table_name}`
- Single `alter_type`: `change {field_name} type in {table_name}`
- Single nullability change: `change {field_name} nullability in {table_name}`
- Multiple changes: `{N} schema changes`

The slug is lowercased, non-alphanumeric characters replaced with underscores, truncated to 40 characters.

---

## Auto-Run on Docker Startup

The generated `backend/app.py` includes a startup event that runs all pending migrations:

```python
@app.on_event('startup')
async def run_migrations():
    if DATABASE_BACKEND != 'postgres':
        return
    # 1. Create _migrations table if not exists
    # 2. Get set of already-applied migration names
    # 3. Scan database/migrations/*.sql (sorted)
    # 4. For each unapplied migration: execute SQL, record in _migrations
```

### The `_migrations` Tracking Table

```sql
CREATE TABLE IF NOT EXISTS _migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Each applied migration is recorded by filename (e.g., `002_add_severity_to_incidents.sql`). On next startup, only migrations not in this table are applied.

If a migration fails, the app exits with `SystemExit(1)` to prevent running with an inconsistent schema.

---

## Column Type Mapping

The SQL writer uses the same type map as the Postgres DDL generator:

| Contract Type | PostgreSQL Type |
|--------------|-----------------|
| `string` | `TEXT` |
| `text` | `TEXT` |
| `integer` | `INTEGER` |
| `number` | `NUMERIC` |
| `boolean` | `BOOLEAN` |
| `datetime` | `TIMESTAMPTZ` |
| `date` | `DATE` |
| `uuid` | `UUID` |
| `email` | `TEXT` |
| `array` | `JSONB` |
| `object` | `JSONB` |

### Auto-Indexed Fields

Certain fields get automatic indexes:
- Reference fields (any field with a `references` property)
- Fields in the `AUTO_INDEX_FIELDS` set from `gen_ddl.py` (e.g., `number`, `created_at`, `status`, `state`)
- The `id` field is always a `PRIMARY KEY` (not separately indexed)

### Special Column Handling

- `id` with type `uuid`: `UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `number`: `TEXT UNIQUE`
- Every table gets a `data JSONB DEFAULT '{}'::jsonb` column for extensible metadata

---

## Python API

### Generate Migrations Programmatically

```python
from pathlib import Path
from forge.ir.compiler import Compiler
from forge.targets.migrations.generator import MigrationGenerator

# Compile contracts to IR
ir = Compiler(contract_root=Path("domains/helpdesk")).compile()

# Generate migration
gen = MigrationGenerator(
    ir_cache_path=Path(".forge/ir_cache"),
    migrations_dir=Path("runtime/database/migrations"),
)
files = gen.generate(ir)

# Write migration files
for f in files:
    path = Path("runtime") / f.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f.content)
    print(f"Generated: {f.path}")
```

### Diff Entities Directly

```python
from forge.targets.migrations.differ import diff_entities

changes = diff_entities(old_ir.entities, new_ir.entities)
for change in changes:
    print(f"{change.change_type}: {change.table_name}.{change.field_name}")
    if change.destructive:
        print("  WARNING: destructive change")
```

### Convert Changes to SQL

```python
from forge.targets.migrations.sql_writer import changes_to_sql, schema_change_to_sql

# All changes at once
sql = changes_to_sql(changes)
print(sql)

# Single change
sql = schema_change_to_sql(changes[0])
print(sql)
```

### Manage the IR Cache

```python
from forge.targets.migrations.ir_cache import save_ir_cache, load_ir_cache

# Save current IR
save_ir_cache(ir, Path(".forge/ir_cache"))

# Load previous IR
previous = load_ir_cache(Path(".forge/ir_cache"))
if previous is None:
    print("No cache exists (first generation)")
```

---

## CLI Usage

```bash
# Generate all code including migrations
specora generate --target all

# Generate only migrations
specora generate --target migrations

# Preview what migrations would be generated (dry run)
specora diff
```

---

## Example: Adding a Field

**1. Add `severity` to the incident entity contract:**

```yaml
# domains/helpdesk/entities/incident.contract.yaml
spec:
  fields:
    severity:
      type: string
      required: true
      enum: [critical, high, medium, low]
      description: "Incident severity"
```

**2. Regenerate:**

```python
from pathlib import Path
from forge.ir.compiler import Compiler
from forge.targets.migrations.generator import MigrationGenerator

ir = Compiler(contract_root=Path("domains/helpdesk")).compile()
gen = MigrationGenerator(
    ir_cache_path=Path(".forge/ir_cache"),
    migrations_dir=Path("runtime/database/migrations"),
)
for f in gen.generate(ir):
    path = Path("runtime") / f.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f.content)
    print(f"Generated: {f.path}")
# Output: Generated: database/migrations/002_add_severity_to_incidents.sql
```

**3. The migration file:**

```sql
-- @generated by Specora Core — do not edit
-- source: domain/helpdesk
-- description: Migration: add severity to incidents

ALTER TABLE incidents ADD COLUMN severity TEXT NOT NULL DEFAULT 'medium';
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents (severity);
```

**4. On next Docker startup**, the migration runner applies it automatically and records `002_add_severity_to_incidents.sql` in the `_migrations` table.

---

## Related Documentation

- [Self-Healing Loop](self-healing-loop.md) -- Migrations are auto-generated after Healer fixes
- [Production Deployment](production-deployment.md) -- Docker startup and migration runner
- [Architecture](architecture.md) -- Where migrations fit in the generator pipeline
- [Frontend Generation](frontend-generation.md) -- The Next.js generator that runs alongside migrations

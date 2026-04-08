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

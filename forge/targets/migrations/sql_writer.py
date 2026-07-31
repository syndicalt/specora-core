"""Convert SchemaChange objects into Postgres SQL statements.

Every statement this module emits is written to be safe to re-apply. That is
what keeps the two halves of the schema story coherent: `database/schema.sql`
bootstraps an empty database with the *current* contracts, and the migration
files carry an existing database forward. A database bootstrapped from
schema.sql can therefore be handed the full migration series without damage —
each file either finds its change already present and does nothing, or applies
it. Which files have run is tracked by the generated entrypoint in
`_specora_migrations`; this module deliberately does not introduce a second
ledger for the same fact.

The DDL itself is not written here. `forge.targets.postgres.gen_ddl` owns
column rendering, foreign keys, indexes and triggers, and this module calls into
it, so a fix to quoting or defaults lands in both outputs at once. Previously
the column renderer was duplicated between the two files and had already
diverged.
"""

from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, FieldIR
from forge.targets.migrations.differ import SchemaChange
from forge.targets.naming import sql_ident, sql_literal
from forge.targets.postgres.gen_ddl import (
    SchemaContext,
    column_is_not_null,
    create_table_sql,
    default_clause,
    foreign_key_statements,
    index_statements,
    updated_at_trigger_statements,
)
from forge.targets.typemap import pg_column_type


def build_context(ir: DomainIR) -> SchemaContext:
    """Resolve the cross-entity facts foreign keys and indexes need."""
    return SchemaContext.from_ir(ir)


def schema_change_to_sql(change: SchemaChange, context: SchemaContext | None = None) -> str:
    """Convert a single SchemaChange to SQL.

    Args:
        change: The change to render.
        context: Cross-entity lookup for foreign keys and declared filters.
            Without it a `create_table` still emits its table, but its foreign
            keys are replaced by a comment naming what could not be resolved —
            the target table is simply not knowable from one `EntityIR`.
    """
    primary, deferred = _render(change, context or SchemaContext.empty())
    return "\n".join([primary, *deferred]) if deferred else primary


def changes_to_sql(changes: list[SchemaChange], context: SchemaContext | None = None) -> str:
    """Convert a list of SchemaChanges to a complete migration SQL body.

    Table-level statements are emitted before constraint and index statements.
    A migration that adds two entities referencing each other would otherwise
    fail on whichever foreign key was written before its target table existed.
    """
    if not changes:
        return "-- No schema changes detected.\n"

    ctx = context or SchemaContext.empty()
    primaries: list[str] = []
    deferrals: list[str] = []

    for change in changes:
        primary, deferred = _render(change, ctx)
        if primary:
            primaries.append(primary)
        deferrals.extend(deferred)

    return "\n\n".join(primaries + deferrals) + "\n"


def _render(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    """Return a change's table-level SQL and its deferred constraint/index SQL."""
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
    if handler is None:
        return f"-- Unknown change type: {change.change_type}", []
    return handler(change, context)


def _create_table(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    entity = change.entity
    if not entity:
        return f"-- Cannot create table {change.table_name}: no entity data", []

    deferred = foreign_key_statements(entity, context, replace=True)
    deferred.extend(index_statements(entity, context, if_not_exists=True))
    deferred.extend(updated_at_trigger_statements([entity], replace=True))
    return create_table_sql(entity, if_not_exists=True), deferred


def _drop_table(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    return (
        f"-- WARNING: DESTRUCTIVE — dropping table {change.table_name}\n"
        f"DROP TABLE IF EXISTS {sql_ident(change.table_name)} CASCADE;",
        [],
    )


def _add_column(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    table = sql_ident(change.table_name)
    field = change.field_ir
    if not field:
        return (
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {sql_ident(change.field_name)} TEXT;",
            [],
        )

    parts = [
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {sql_ident(field.name)}",
        pg_column_type(field.type, field.constraints),
    ]
    not_null = column_is_not_null(field)
    if not_null:
        parts.append("NOT NULL")
    clause = default_clause(field)
    if clause:
        parts.append(clause)

    lines: list[str] = []
    if not_null and not clause:
        # Postgres rejects adding a NOT NULL column with no default to a table
        # that already has rows, and there is no value the contract authorises
        # us to backfill with. Emitting it anyway makes the migration fail
        # loudly at apply time; quietly emitting a nullable column instead would
        # ship a database that contradicts the contract.
        lines.append(
            f"-- WARNING: {change.table_name}.{field.name} is required but declares no default. "
            f"This statement fails on a non-empty table; add a default to the contract, or "
            f"backfill and add the constraint by hand."
        )
    lines.append(" ".join(parts) + ";")

    deferred: list[str] = []
    if field.reference:
        entity_stub = _single_field_entity(change.table_name, field)
        deferred.extend(foreign_key_statements(entity_stub, context, replace=True))
    return "\n".join(lines), deferred


def _drop_column(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    return (
        f"-- WARNING: DESTRUCTIVE — dropping column {change.field_name}\n"
        f"ALTER TABLE {sql_ident(change.table_name)} "
        f"DROP COLUMN IF EXISTS {sql_ident(change.field_name)};",
        [],
    )


def _alter_type(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    constraints = change.field_ir.constraints if change.field_ir else {}
    new_type = pg_column_type(change.new_value, constraints)
    column = sql_ident(change.field_name)
    return (
        f"ALTER TABLE {sql_ident(change.table_name)} "
        f"ALTER COLUMN {column} TYPE {new_type} "
        f"USING {column}::{new_type};",
        [],
    )


def _set_not_null(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    # A DEFAULT does not backfill rows that already exist, so this fails if any
    # of them hold NULL. That failure is the correct outcome — the alternative is
    # a database that contradicts the contract — but the operator needs to know
    # a backfill may be required first.
    return (
        f"-- NOTE: fails if existing rows have NULL {change.table_name}.{change.field_name}; "
        f"backfill first.\n"
        f"ALTER TABLE {sql_ident(change.table_name)} "
        f"ALTER COLUMN {sql_ident(change.field_name)} SET NOT NULL;",
        [],
    )


def _drop_not_null(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    return (
        f"ALTER TABLE {sql_ident(change.table_name)} "
        f"ALTER COLUMN {sql_ident(change.field_name)} DROP NOT NULL;",
        [],
    )


def _set_default(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    # Rendered from the field, not from `new_value`: a server-side default is an
    # expression (`now()`), and passing it through sql_literal would install the
    # string 'now()' as the default instead of calling the function.
    clause = default_clause(change.field_ir) if change.field_ir else ""
    if not clause:
        clause = f"DEFAULT {sql_literal(change.new_value)}"
    return (
        f"ALTER TABLE {sql_ident(change.table_name)} "
        f"ALTER COLUMN {sql_ident(change.field_name)} SET {clause};",
        [],
    )


def _drop_default(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    return (
        f"ALTER TABLE {sql_ident(change.table_name)} "
        f"ALTER COLUMN {sql_ident(change.field_name)} DROP DEFAULT;",
        [],
    )


def _add_index(change: SchemaChange, context: SchemaContext) -> tuple[str, list[str]]:
    return (
        f"CREATE INDEX IF NOT EXISTS "
        f"{sql_ident(f'idx_{change.table_name}_{change.field_name}')} "
        f"ON {sql_ident(change.table_name)} ({sql_ident(change.field_name)});",
        [],
    )


def _single_field_entity(table: str, field: FieldIR) -> EntityIR:
    """Wrap one field as an EntityIR so the shared FK renderer can consume it.

    `foreign_key_statements` works over an entity; an `add_column` change only
    carries the one field. Reusing the renderer is worth this adapter — the
    alternative is a second place that knows how to spell a FOREIGN KEY.
    """
    return EntityIR(fqn=f"table/{table}", name=table, domain="", table_name=table, fields=[field])

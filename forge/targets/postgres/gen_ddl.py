"""PostgreSQL DDL generator — EntityIR -> a bootstrap schema.

`schema.sql` is **bootstrap-only**: it is the schema for an empty database and
nothing else. It is what `docker-entrypoint-initdb.d` runs on first boot. Every
change after that first boot travels through `forge.targets.migrations`, which
diffs the cached IR and emits a numbered `ALTER`. The two halves share the DDL
emitters in this module so they cannot drift apart.

That split is why the `CREATE TABLE` statements here carry no `IF NOT EXISTS`.
The guard used to be unconditional, which meant regenerating after a contract
change produced a schema that applied cleanly to an existing database and
changed nothing in it — the contract said one thing, the database said another,
and no error was raised anywhere. Applied to its intended target (an empty
database) an unguarded `CREATE TABLE` cannot fail; applied to a populated one it
now fails loudly, which is the correct outcome. Migration files keep the guards,
because they may legitimately be re-run.

Type mapping lives in `forge.targets.typemap`; identifier and literal quoting
lives in `forge.targets.naming`. Neither is reimplemented here.

Structure of the emitted file, in dependency order:

    1. `schema_migrations` ledger
    2. `CREATE TABLE` for every entity
    3. `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` for every reference
    4. `CREATE INDEX` for the queries the generated app actually issues
    5. `updated_at` triggers

Foreign keys are a separate pass rather than inline `REFERENCES` clauses so that
entity declaration order is irrelevant and reference cycles are representable.

Usage:
    from forge.targets.postgres.gen_ddl import PostgresGenerator

    gen = PostgresGenerator()
    files = gen.generate(ir)
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.ir.model import DomainIR, EntityIR, FieldIR
from forge.targets.base import BaseGenerator, GeneratedFile, GenerationError, provenance_header
from forge.targets.naming import sql_ident, sql_literal
from forge.targets.typemap import pg_column_type

# The generated repositories page with `ORDER BY created_at DESC, id DESC` and a
# keyset cursor (see docs/CODEGEN_CONTRACT.md §7), so this pair is the sort key
# every list endpoint scans. `id` breaks ties on identical timestamps, which is
# what makes the cursor total and therefore stable across pages.
KEYSET_SORT_FIELD = "created_at"
KEYSET_TIEBREAK_FIELD = "id"

# `RESTRICT` rather than `CASCADE` or `SET NULL`: a contract's `references:`
# block expresses that a relationship exists, not what should happen to the
# child rows when the parent disappears. Inferring `CASCADE` from silence would
# let one `DELETE /customers/{id}` destroy every ticket that customer ever
# filed, and `SET NULL` would silently blank a column the contract marked
# required. `RESTRICT` is the only option that cannot lose data without the
# operator saying so; the delete fails until the children are dealt with.
# Entities that need soft deletion already have `mixin/stdlib/soft_deletable`.
FK_ON_DELETE = "RESTRICT"

_UPDATED_AT_FUNCTION_PREFIX = "specora_set_"


def _comment(text: str) -> str:
    """Render text as a single-line SQL comment.

    Contract descriptions are frequently YAML folded scalars, so they arrive
    with embedded newlines. Interpolating one straight into a `--` comment
    turns the remainder into executable SQL.
    """
    flattened = " ".join(text.split())
    return f"-- {flattened}"


@dataclass(frozen=True)
class SchemaContext:
    """Cross-entity facts a single entity's DDL cannot supply on its own.

    Foreign keys need the *target* entity's table and primary key, and index
    selection needs the filters declared by Page and Route contracts. Both live
    outside the `EntityIR` being rendered, so they are resolved once from the
    whole `DomainIR` and threaded through.
    """

    table_by_entity: dict[str, str]
    # FQN -> (primary key column, its PostgreSQL type). The type is carried so a
    # foreign key can be rejected when the two sides cannot be compared.
    primary_key_by_entity: dict[str, tuple[str, str]]
    filter_fields_by_entity: dict[str, frozenset[str]]

    @classmethod
    def empty(cls) -> SchemaContext:
        return cls({}, {}, {})

    @classmethod
    def from_ir(cls, ir: DomainIR) -> SchemaContext:
        tables = {e.fqn: e.table_name for e in ir.entities if e.table_name}
        primary_keys: dict[str, tuple[str, str]] = {}
        for entity in ir.entities:
            for f in entity.fields:
                if f.name == KEYSET_TIEBREAK_FIELD and f.type == "uuid":
                    primary_keys[entity.fqn] = (f.name, pg_column_type(f.type, f.constraints))
                    break
        return cls(tables, primary_keys, _declared_filter_fields(ir))


def _declared_filter_fields(ir: DomainIR) -> dict[str, frozenset[str]]:
    """Collect, per entity, the field names some contract declares filterable.

    Sources are the Page contract's `filters` block and its views' `filterable`
    lists, plus a Route contract's `global_behaviors.filters`. Everything is
    intersected with the entity's real field names, because those blocks also
    carry named saved-filter identifiers (`quick: [my_checkouts]`) that are not
    columns at all.
    """
    fields_by_entity = {e.fqn: {f.name for f in e.fields} for e in ir.entities}
    declared: dict[str, set[str]] = {fqn: set() for fqn in fields_by_entity}

    def record(entity_fqn: str, candidates: object) -> None:
        if entity_fqn not in declared or not isinstance(candidates, list):
            return
        declared[entity_fqn].update(
            c for c in candidates if isinstance(c, str) and c in fields_by_entity[entity_fqn]
        )

    for page in ir.pages:
        for group in page.filters.values():
            record(page.entity_fqn, group)
        for view in page.views:
            if isinstance(view, dict):
                record(page.entity_fqn, view.get("filterable"))

    for route in ir.routes:
        record(route.entity_fqn, route.global_behaviors.get("filters"))

    return {fqn: frozenset(names) for fqn, names in declared.items()}


def default_clause(field: FieldIR) -> str:
    """Render a column's `DEFAULT`, or an empty string when it has none.

    Two distinct sources feed this. `computed` names a server-side expression
    and must be emitted unquoted; `default` is a contract-authored *value* and
    must be escaped, because contracts are routinely LLM-authored and a
    hallucinated `O'Brien` would otherwise both break the schema and provide an
    injection point.

    A `computed` timestamp previously produced a `NOT NULL` column with no
    default, so any writer other than the generated repository — psql, a seed
    script, a second service — could not insert a row at all.
    """
    if field.computed in ("now", "now_on_update"):
        return "DEFAULT now()"
    if field.computed == "uuid" and field.type == "uuid":
        return "DEFAULT gen_random_uuid()"
    if field.default is None or field.default == "":
        return ""
    return f"DEFAULT {sql_literal(field.default)}"


def column_definition(field: FieldIR) -> str:
    """Render one column of a `CREATE TABLE` body, indented."""
    ident = sql_ident(field.name)

    if field.name == KEYSET_TIEBREAK_FIELD and field.type == "uuid":
        return f"    {ident} UUID PRIMARY KEY DEFAULT gen_random_uuid()"

    # `number` from mixin/stdlib/identifiable is the human-facing sequential
    # key. UNIQUE already builds an index, so no CREATE INDEX is emitted for it.
    if field.name == "number":
        return f"    {ident} TEXT UNIQUE"

    parts = [f"    {ident}", pg_column_type(field.type, field.constraints)]
    if field.required:
        parts.append("NOT NULL")
    clause = default_clause(field)
    if clause:
        parts.append(clause)
    return " ".join(parts)


def create_table_sql(entity: EntityIR, *, if_not_exists: bool) -> str:
    """Render `CREATE TABLE` for one entity, with a provenance comment.

    Raises:
        GenerationError: If the entity has no fields. An empty column list is
            not valid SQL, and emitting it would move a contract-authoring
            mistake to deploy time.
    """
    if not entity.fields:
        raise GenerationError(
            f"Entity {entity.fqn!r} declares no fields, so it cannot become a "
            f"table — `CREATE TABLE {entity.table_name} ()` is not valid SQL. "
            f"Add fields, or add mixin/stdlib/identifiable."
        )

    lines = [_comment(f"Entity: {entity.name} ({entity.fqn})")]
    if entity.description:
        lines.append(_comment(entity.description))

    guard = "IF NOT EXISTS " if if_not_exists else ""
    lines.append(f"CREATE TABLE {guard}{sql_ident(entity.table_name)} (")
    lines.append(",\n".join(column_definition(f) for f in entity.fields))
    lines.append(");")
    return "\n".join(lines)


def foreign_key_statements(
    entity: EntityIR,
    context: SchemaContext,
    *,
    replace: bool,
) -> list[str]:
    """Render the `FOREIGN KEY` constraints for one entity's reference fields.

    `ReferenceIR` has always documented itself as driving "a foreign key
    constraint"; until now nothing emitted one, so a generated database had no
    referential integrity of any kind and an `assigned_agent_id` could point at
    a UUID that never existed.

    These are `ALTER TABLE` statements rather than inline `REFERENCES` clauses
    so that they can all run after every table exists. Inline clauses would make
    the output order-dependent and would make a reference cycle — `user.manager_id`
    pointing back at `users`, or two entities referencing each other —
    unrepresentable.

    Args:
        entity: The referencing entity.
        context: Supplies the target table and primary key.
        replace: Emit a `DROP CONSTRAINT IF EXISTS` first. Postgres has no
            `ADD CONSTRAINT IF EXISTS`, and migration files must tolerate being
            re-applied; the bootstrap schema runs exactly once and does not.

    Raises:
        GenerationError: If the referencing column's type cannot be compared
            with the target's primary key. Postgres rejects such a constraint
            outright, so emitting it would move the failure to deploy time.
    """
    statements: list[str] = []

    for field in entity.fields:
        if not field.reference:
            continue

        target_fqn = field.reference.target_entity
        target_table = context.table_by_entity.get(target_fqn)
        target = context.primary_key_by_entity.get(target_fqn)

        if not target_table or not target:
            statements.append(
                _comment(
                    f"No FOREIGN KEY for {entity.table_name}.{field.name}: reference target "
                    f"{target_fqn} is not part of this build, so its table and primary key "
                    f"are unknown here."
                )
            )
            continue

        target_pk, target_pk_type = target
        column_type = pg_column_type(field.type, field.constraints)
        if column_type != target_pk_type:
            raise GenerationError(
                f"{entity.fqn} field {field.name!r} references {target_fqn}, whose primary "
                f"key is {target_pk_type}, but the field is declared "
                f"{field.type!r} ({column_type}). PostgreSQL cannot build a foreign key "
                f"between incomparable types. Declare the field as `type: uuid`."
            )

        constraint = sql_ident(f"fk_{entity.table_name}_{field.name}")
        table = sql_ident(entity.table_name)
        if replace:
            statements.append(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint};")
        statements.append(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY ({sql_ident(field.name)}) "
            f"REFERENCES {sql_ident(target_table)} ({sql_ident(target_pk)}) "
            f"ON DELETE {FK_ON_DELETE};"
        )

    return statements


def index_statements(
    entity: EntityIR,
    context: SchemaContext,
    *,
    if_not_exists: bool,
) -> list[str]:
    """Render the indexes for one entity, derived from the queries it will serve.

    The previous rule indexed a fixed name list — `state`, `status`, `priority`,
    `created_at`, `updated_at`, `email`, `number` — on every table regardless of
    what the generated code queried. That both over-indexed (`updated_at` is
    written on every update and read by nothing; `number` is already UNIQUE, so
    it got a second, redundant index) and under-indexed (a bare `created_at`
    index does not fully serve the two-column keyset sort, and a filtered list
    query still had to sort).

    What the generated app actually issues is
    `... [WHERE <filter> = $n] ORDER BY created_at DESC, id DESC LIMIT $1`,
    plus point lookups on reference columns. So:

      - one composite `(created_at DESC, id DESC)` matching the keyset sort;
      - `(filter, created_at DESC, id DESC)` per contract-declared filter, which
        serves the filtered list query without a sort *and* serves equality on
        the filter alone as a leftmost prefix, so no separate single-column
        index is needed;
      - a plain index on each reference column, for joins and for
        "children of this parent" lookups.

    `id` is skipped (the primary key indexes it), `number` is skipped (UNIQUE
    indexes it), and `created_at` is skipped as a standalone (it leads the
    keyset index).
    """
    table = sql_ident(entity.table_name)
    guard = "IF NOT EXISTS " if if_not_exists else ""
    names = {f.name for f in entity.fields}

    has_keyset = KEYSET_SORT_FIELD in names and KEYSET_TIEBREAK_FIELD in names
    sort_suffix = (
        [f"{sql_ident(KEYSET_SORT_FIELD)} DESC", f"{sql_ident(KEYSET_TIEBREAK_FIELD)} DESC"]
        if has_keyset
        else []
    )

    specs: list[tuple[str, list[str]]] = []
    if has_keyset:
        specs.append((f"idx_{entity.table_name}_keyset", list(sort_suffix)))

    filters = context.filter_fields_by_entity.get(entity.fqn, frozenset())
    for field in entity.fields:
        if field.name in (KEYSET_TIEBREAK_FIELD, KEYSET_SORT_FIELD, "number"):
            continue
        is_filter = field.name in filters
        if not is_filter and not field.reference:
            continue
        columns = [sql_ident(field.name)] + (sort_suffix if is_filter else [])
        specs.append((f"idx_{entity.table_name}_{field.name}", columns))

    return [
        f"CREATE INDEX {guard}{sql_ident(name)} ON {table} ({', '.join(columns)});"
        for name, columns in specs
    ]


def updated_at_trigger_statements(entities: list[EntityIR], *, replace: bool) -> list[str]:
    """Render triggers that maintain `computed: now_on_update` columns.

    `DEFAULT now()` covers the insert. Nothing covers the update, and the
    generated repository is not the only writer a production database ever has,
    so leaving it to application code means `updated_at` silently goes stale for
    every other writer. A `BEFORE UPDATE` trigger makes the column mean what the
    contract says it means regardless of who writes.

    One function is emitted per distinct column name (in practice exactly one,
    `updated_at`) because plpgsql cannot assign to a `NEW` field chosen at
    runtime without dynamic SQL.
    """
    columns = sorted(
        {f.name for e in entities for f in e.fields if f.computed == "now_on_update"}
    )
    if not columns:
        return []

    statements: list[str] = []
    for column in columns:
        function = sql_ident(f"{_UPDATED_AT_FUNCTION_PREFIX}{column}")
        statements.append(
            f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger\n"
            f"LANGUAGE plpgsql AS $$\n"
            f"BEGIN\n"
            f"    NEW.{sql_ident(column)} := now();\n"
            f"    RETURN NEW;\n"
            f"END;\n"
            f"$$;"
        )

    for entity in entities:
        for column in columns:
            if not any(f.name == column and f.computed == "now_on_update" for f in entity.fields):
                continue
            table = sql_ident(entity.table_name)
            trigger = sql_ident(f"trg_{entity.table_name}_{column}")
            function = sql_ident(f"{_UPDATED_AT_FUNCTION_PREFIX}{column}")
            if replace:
                statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table};")
            statements.append(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table}\n"
                f"FOR EACH ROW EXECUTE FUNCTION {function}();"
            )

    return statements


BOOTSTRAP_NOTICE = """-- ---------------------------------------------------------------------------
-- BOOTSTRAP ONLY. Apply this file to an EMPTY database, exactly once.
--
-- It is not an upgrade script, and it deliberately omits IF NOT EXISTS on the
-- entity tables. Run it against a database that already has them and it fails
-- rather than succeeding while changing nothing. Silent no-ops are how a schema
-- drifts away from the contracts that are supposed to define it.
--
-- To evolve an existing database, apply database/migrations/NNN_*.sql in
-- ascending order. Those files are guarded and safe to re-apply, so a database
-- bootstrapped from this file can also be handed the full migration series
-- without harm. The generated entrypoint.sh does exactly this: schema.sql on a
-- fresh database, pending migrations otherwise, tracked in _specora_migrations.
-- ---------------------------------------------------------------------------"""


class PostgresGenerator(BaseGenerator):
    """Generates the bootstrap PostgreSQL schema from entity definitions."""

    def name(self) -> str:
        return "postgres"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        """Generate a single schema.sql file with all table definitions.

        Args:
            ir: The compiled DomainIR.

        Returns:
            List containing one GeneratedFile (schema.sql).
        """
        if not ir.entities:
            return []

        context = SchemaContext.from_ir(ir)
        provenance_fqns = ", ".join(e.fqn for e in ir.entities)
        header = provenance_header(
            "sql",
            provenance_fqns,
            f"PostgreSQL bootstrap schema for the {ir.domain} domain",
        )

        sections: list[str] = [BOOTSTRAP_NOTICE, ""]

        for entity in ir.entities:
            sections.append(create_table_sql(entity, if_not_exists=False))
            sections.append("")

        constraints = [
            stmt
            for entity in ir.entities
            for stmt in foreign_key_statements(entity, context, replace=False)
        ]
        if constraints:
            sections.append(_comment("Foreign keys, after every table exists."))
            sections.extend(constraints)
            sections.append("")

        indexes = [
            stmt
            for entity in ir.entities
            for stmt in index_statements(entity, context, if_not_exists=False)
        ]
        if indexes:
            sections.append(_comment("Indexes matching the queries the generated app issues."))
            sections.extend(indexes)
            sections.append("")

        triggers = updated_at_trigger_statements(ir.entities, replace=False)
        if triggers:
            sections.append(_comment("Maintain computed: now_on_update columns for every writer."))
            sections.extend(triggers)
            sections.append("")

        content = header + "\n".join(sections).rstrip("\n") + "\n"

        return [
            GeneratedFile(
                path="database/schema.sql",
                content=content,
                provenance=provenance_fqns,
            )
        ]

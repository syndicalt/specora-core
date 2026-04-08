"""PostgreSQL DDL generator — EntityIR -> CREATE TABLE statements.

Generates SQL DDL (Data Definition Language) for all entities in the
domain. Each entity becomes a table with properly typed columns,
NOT NULL constraints, and indexes on common fields.

Type mapping (IR -> PostgreSQL):
    string   -> TEXT
    integer  -> INTEGER
    number   -> NUMERIC
    boolean  -> BOOLEAN
    text     -> TEXT
    array    -> JSONB
    object   -> JSONB
    datetime -> TIMESTAMPTZ
    date     -> DATE
    uuid     -> UUID
    email    -> TEXT

Special handling:
    - `id` fields with type `uuid` get `UUID PRIMARY KEY DEFAULT gen_random_uuid()`
    - `number` fields (sequential IDs) get `TEXT UNIQUE`
    - Fields with `required: true` get `NOT NULL`
    - Reference fields get indexes for join performance
    - State fields get indexes for filter queries

Usage:
    from forge.targets.postgres.gen_ddl import PostgresGenerator

    gen = PostgresGenerator()
    files = gen.generate(ir)
"""

from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, FieldIR
from forge.targets.base import BaseGenerator, GeneratedFile, provenance_header

# IR type -> PostgreSQL column type
PG_TYPE_MAP: dict[str, str] = {
    "string": "TEXT",
    "integer": "INTEGER",
    "number": "NUMERIC",
    "boolean": "BOOLEAN",
    "text": "TEXT",
    "array": "JSONB",
    "object": "JSONB",
    "datetime": "TIMESTAMPTZ",
    "date": "DATE",
    "uuid": "UUID",
    "email": "TEXT",
}

# Fields that automatically get indexes
AUTO_INDEX_FIELDS = {"state", "status", "priority", "created_at", "updated_at", "email", "number"}


class PostgresGenerator(BaseGenerator):
    """Generates PostgreSQL DDL from entity definitions."""

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

        provenance_fqns = ", ".join(e.fqn for e in ir.entities)
        header = provenance_header(
            "sql",
            provenance_fqns,
            f"PostgreSQL schema for the {ir.domain} domain",
        )

        statements: list[str] = []

        # Extension for UUID generation
        statements.append("-- Enable UUID generation")
        statements.append('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
        statements.append("")

        for entity in ir.entities:
            statements.append(self._generate_table(entity))
            statements.extend(self._generate_indexes(entity))
            statements.append("")

        content = header + "\n".join(statements) + "\n"

        return [
            GeneratedFile(
                path="database/schema.sql",
                content=content,
                provenance=provenance_fqns,
            )
        ]

    def _generate_table(self, entity: EntityIR) -> str:
        """Generate a CREATE TABLE statement for an entity.

        Args:
            entity: The EntityIR to convert.

        Returns:
            SQL CREATE TABLE IF NOT EXISTS statement.
        """
        lines = [
            f"-- Entity: {entity.name} ({entity.fqn})",
        ]
        if entity.description:
            lines.append(f"-- {entity.description}")

        lines.append(f"CREATE TABLE IF NOT EXISTS {entity.table_name} (")

        columns: list[str] = []
        for field in entity.fields:
            columns.append(self._generate_column(field))

        # Add a JSONB data column for extensibility
        columns.append("    data JSONB DEFAULT '{}'::jsonb")

        lines.append(",\n".join(columns))
        lines.append(");")

        return "\n".join(lines)

    def _generate_column(self, field: FieldIR) -> str:
        """Generate a SQL column definition.

        Args:
            field: The FieldIR to convert.

        Returns:
            SQL column line (e.g., "    name TEXT NOT NULL").
        """
        pg_type = PG_TYPE_MAP.get(field.type, "TEXT")

        # Special handling for ID fields
        if field.name == "id" and field.type == "uuid":
            return "    id UUID PRIMARY KEY DEFAULT gen_random_uuid()"

        # Special handling for sequential number fields
        if field.name == "number":
            return "    number TEXT UNIQUE"

        parts = [f"    {field.name}", pg_type]

        # NOT NULL for required fields
        if field.required:
            parts.append("NOT NULL")

        # Default values
        if field.default is not None and field.default != "":
            if isinstance(field.default, bool):
                parts.append(f"DEFAULT {'TRUE' if field.default else 'FALSE'}")
            elif isinstance(field.default, (int, float)):
                parts.append(f"DEFAULT {field.default}")
            elif isinstance(field.default, str):
                parts.append(f"DEFAULT '{field.default}'")
            elif isinstance(field.default, list):
                parts.append("DEFAULT '[]'::jsonb")

        return " ".join(parts)

    def _generate_indexes(self, entity: EntityIR) -> list[str]:
        """Generate CREATE INDEX statements for common fields.

        Indexes are created for:
        - Fields in AUTO_INDEX_FIELDS
        - Reference fields (for join performance)

        Args:
            entity: The EntityIR to generate indexes for.

        Returns:
            List of SQL CREATE INDEX statements.
        """
        indexes: list[str] = []

        for field in entity.fields:
            needs_index = False

            # Auto-index common fields
            if field.name in AUTO_INDEX_FIELDS:
                needs_index = True

            # Index reference fields
            if field.reference:
                needs_index = True

            # Skip id (already has PK index) and computed fields
            if field.name == "id":
                needs_index = False

            if needs_index:
                idx_name = f"idx_{entity.table_name}_{field.name}"
                indexes.append(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {entity.table_name} ({field.name});"
                )

        return indexes

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
        from forge.targets.migrations.differ import SchemaChange

        statements = ['CREATE EXTENSION IF NOT EXISTS "pgcrypto";\n']
        for entity in ir.entities:
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

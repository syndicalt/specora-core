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

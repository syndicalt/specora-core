"""Tests for the production FastAPI generator."""
import pytest

from forge.ir.model import DomainIR


@pytest.fixture
def empty_ir() -> DomainIR:
    return DomainIR(domain="test")


class TestGenConfig:

    def test_generates_config(self, empty_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_config import generate_config
        result = generate_config(empty_ir)
        assert result.path == "backend/config.py"
        assert "DATABASE_URL" in result.content
        assert "DATABASE_BACKEND" in result.content
        assert "AUTH_ENABLED" in result.content
        assert "CORS_ORIGINS" in result.content
        assert "@generated" in result.content


from forge.ir.model import EntityIR, FieldIR


@pytest.fixture
def task_entity() -> EntityIR:
    return EntityIR(
        fqn="entity/test/task",
        name="task",
        domain="test",
        description="A task",
        table_name="tasks",
        fields=[
            FieldIR(name="title", type="string", required=True),
            FieldIR(name="priority", type="string", required=True, enum_values=["high", "medium", "low"]),
            FieldIR(name="id", type="uuid", computed="uuid"),
            FieldIR(name="created_at", type="datetime", computed="now"),
            FieldIR(name="updated_at", type="datetime", computed="now_on_update"),
        ],
    )


@pytest.fixture
def task_ir(task_entity: EntityIR) -> DomainIR:
    return DomainIR(domain="test", entities=[task_entity])


class TestGenRepositories:

    def test_generates_base_repository(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories
        files = generate_repositories(task_ir)
        base = next(f for f in files if "base.py" in f.path)
        assert "class TaskRepository" in base.content
        assert "async def list" in base.content
        assert "async def get" in base.content
        assert "async def create" in base.content
        assert "async def update" in base.content
        assert "async def delete" in base.content

    def test_generates_memory_adapter(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories
        files = generate_repositories(task_ir)
        mem = next(f for f in files if "memory.py" in f.path)
        assert "class MemoryTaskRepository" in mem.content
        assert "_store" in mem.content

    def test_generates_postgres_adapter(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories
        files = generate_repositories(task_ir)
        pg = next(f for f in files if "postgres.py" in f.path)
        assert "class PostgresTaskRepository" in pg.content
        assert "asyncpg" in pg.content or "SELECT" in pg.content

    def test_generates_provider_factory(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories
        files = generate_repositories(task_ir)
        base = next(f for f in files if "base.py" in f.path)
        assert "def get_task_repo" in base.content

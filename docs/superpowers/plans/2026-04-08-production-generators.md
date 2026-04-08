# Production-Grade Code Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace in-memory store generators with production-grade code generation: repository pattern for DB abstraction, pluggable auth, Docker deployment, and black-box test generation — all from the same contracts.

**Architecture:** New `fastapi-prod` generator produces a complete app with abstract repository interfaces, concrete Postgres/memory adapters, optional JWT auth from infra/auth contracts, Docker files, and pytest test suites. The existing generators remain for backward compatibility.

**Tech Stack:** Python 3.10+, FastAPI, asyncpg, Pydantic v2, python-jose, passlib, Docker, pytest

**Spec:** `docs/superpowers/specs/2026-04-08-production-generators-design.md`
**Issue:** syndicalt/specora-core#6

---

## File Map

### New generator files (in specora-core)

| File | Responsibility |
|------|---------------|
| `forge/targets/fastapi_prod/__init__.py` | Package init |
| `forge/targets/fastapi_prod/gen_config.py` | Generate config.py (12-factor env) |
| `forge/targets/fastapi_prod/gen_models.py` | Generate Pydantic models (improved) |
| `forge/targets/fastapi_prod/gen_repositories.py` | Generate repository base + postgres + memory adapters |
| `forge/targets/fastapi_prod/gen_auth.py` | Generate auth system from infra/auth contract |
| `forge/targets/fastapi_prod/gen_routes.py` | Generate route handlers (call repos, inject auth) |
| `forge/targets/fastapi_prod/gen_app.py` | Generate app.py with middleware stack |
| `forge/targets/fastapi_prod/gen_docker.py` | Generate Dockerfile, docker-compose.yml, .env.example, requirements.txt |
| `forge/targets/fastapi_prod/gen_tests.py` | Generate black-box pytest tests |
| `forge/targets/fastapi_prod/generator.py` | FastAPIProductionGenerator — orchestrates all sub-generators |
| `tests/test_targets/test_fastapi_prod.py` | Tests for the production generator |

---

### Task 1: Config Generator

**Files:**
- Create: `forge/targets/fastapi_prod/__init__.py`
- Create: `forge/targets/fastapi_prod/gen_config.py`
- Create: `tests/test_targets/test_fastapi_prod.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_targets/test_fastapi_prod.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_targets/test_fastapi_prod.py::TestGenConfig -v`

- [ ] **Step 3: Implement config generator**

```python
# forge/targets/fastapi_prod/__init__.py
# (empty)
```

```python
# forge/targets/fastapi_prod/gen_config.py
"""Generate 12-factor configuration module."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile, provenance_header


def generate_config(ir: DomainIR) -> GeneratedFile:
    """Generate backend/config.py with environment-based configuration."""
    header = provenance_header("python", f"domain/{ir.domain}", "12-factor environment configuration")

    # Check if auth infra contract exists
    has_auth = any(i.category == "auth" for i in ir.infra)

    lines = [
        header,
        "import os",
        "",
        "",
        "# Database",
        'DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://specora:specora@localhost:5432/specora")',
        'DATABASE_BACKEND = os.getenv("DATABASE_BACKEND", "postgres")',
        "",
        "# Server",
        'PORT = int(os.getenv("PORT", "8000"))',
        'CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")',
        "",
        "# Auth",
        f'AUTH_ENABLED = os.getenv("AUTH_ENABLED", "{"true" if has_auth else "false"}").lower() in ("true", "1")',
        'AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "jwt")',
        'AUTH_SECRET = os.getenv("AUTH_SECRET", "change-me-in-production")',
        'AUTH_TOKEN_EXPIRE_MINUTES = int(os.getenv("AUTH_TOKEN_EXPIRE_MINUTES", "60"))',
        "",
    ]

    return GeneratedFile(
        path="backend/config.py",
        content="\n".join(lines),
        provenance=f"domain/{ir.domain}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_targets/test_fastapi_prod.py::TestGenConfig -v`

- [ ] **Step 5: Commit**

```bash
git add forge/targets/fastapi_prod/ tests/test_targets/test_fastapi_prod.py
git commit -m "feat(#6/T1): config generator — 12-factor environment configuration"
```

---

### Task 2: Repository Base + Memory Adapter

**Files:**
- Create: `forge/targets/fastapi_prod/gen_repositories.py`
- Modify: `tests/test_targets/test_fastapi_prod.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_targets/test_fastapi_prod.py`:

```python
from forge.ir.model import DomainIR, EntityIR, FieldIR


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
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement repository generator**

```python
# forge/targets/fastapi_prod/gen_repositories.py
"""Generate repository interfaces and adapters."""
from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR
from forge.targets.base import GeneratedFile, provenance_header

PYTHON_TYPE_MAP = {
    "string": "str", "integer": "int", "number": "float", "boolean": "bool",
    "text": "str", "array": "list", "object": "dict", "datetime": "str",
    "date": "str", "uuid": "str", "email": "str",
}


def generate_repositories(ir: DomainIR) -> list[GeneratedFile]:
    """Generate repository base, memory adapter, and postgres adapter."""
    if not ir.entities:
        return []
    return [
        _generate_base(ir),
        _generate_memory(ir),
        _generate_postgres(ir),
    ]


def _to_class(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def _generate_base(ir: DomainIR) -> GeneratedFile:
    fqns = ", ".join(e.fqn for e in ir.entities)
    header = provenance_header("python", fqns, "Abstract repository interfaces")

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "from abc import ABC, abstractmethod",
        "from typing import Any, Optional",
        "",
        "",
    ]

    # One abstract class per entity
    for entity in ir.entities:
        cls = _to_class(entity.name)
        lines.append(f"class {cls}Repository(ABC):")
        lines.append(f'    """Repository interface for {entity.name}."""')
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def list(self, limit: int = 100, offset: int = 0, filters: dict | None = None) -> tuple[list[dict], int]: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def get(self, id: str) -> dict | None: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def create(self, data: dict) -> dict: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def update(self, id: str, data: dict) -> dict | None: ...")
        lines.append("")
        lines.append("    @abstractmethod")
        lines.append("    async def delete(self, id: str) -> bool: ...")
        lines.append("")
        if entity.state_machine:
            lines.append("    @abstractmethod")
            lines.append("    async def transition(self, id: str, new_state: str) -> dict | None: ...")
            lines.append("")
        lines.append("")

    # Provider factory functions
    lines.append("# Repository provider factories — wire to config")
    lines.append("# Import the concrete adapter based on DATABASE_BACKEND")
    lines.append("")
    for entity in ir.entities:
        cls = _to_class(entity.name)
        name = entity.name
        lines.append(f"def get_{name}_repo() -> {cls}Repository:")
        lines.append(f"    from backend.config import DATABASE_BACKEND")
        lines.append(f'    if DATABASE_BACKEND == "postgres":')
        lines.append(f"        from backend.repositories.postgres import Postgres{cls}Repository")
        lines.append(f"        return Postgres{cls}Repository()")
        lines.append(f"    from backend.repositories.memory import Memory{cls}Repository")
        lines.append(f"    return Memory{cls}Repository()")
        lines.append("")
        lines.append("")

    return GeneratedFile(
        path="backend/repositories/base.py",
        content="\n".join(lines),
        provenance=fqns,
    )


def _generate_memory(ir: DomainIR) -> GeneratedFile:
    fqns = ", ".join(e.fqn for e in ir.entities)
    header = provenance_header("python", fqns, "In-memory repository adapters (dev/test)")

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import uuid",
        "from datetime import datetime, timezone",
        "from typing import Any, Optional",
        "",
        "from backend.repositories.base import (",
        "    " + ",\n    ".join(f"{_to_class(e.name)}Repository" for e in ir.entities),
        ")",
        "",
        "",
    ]

    for entity in ir.entities:
        cls = _to_class(entity.name)
        table = entity.table_name
        initial_state = entity.state_machine.initial if entity.state_machine else None
        transitions = entity.state_machine.transitions if entity.state_machine else {}

        lines.append(f"class Memory{cls}Repository({cls}Repository):")
        lines.append(f'    """In-memory adapter for {entity.name}."""')
        lines.append("")
        lines.append(f"    _store: dict[str, dict] = {{}}")
        lines.append("")
        lines.append("    async def list(self, limit: int = 100, offset: int = 0, filters: dict | None = None) -> tuple[list[dict], int]:")
        lines.append("        items = list(self._store.values())")
        lines.append("        if filters:")
        lines.append("            items = [i for i in items if all(i.get(k) == v for k, v in filters.items())]")
        lines.append("        return items[offset:offset + limit], len(items)")
        lines.append("")
        lines.append("    async def get(self, id: str) -> dict | None:")
        lines.append("        return self._store.get(id)")
        lines.append("")
        lines.append("    async def create(self, data: dict) -> dict:")
        lines.append('        if "id" not in data:')
        lines.append('            data["id"] = str(uuid.uuid4())')
        lines.append('        data["created_at"] = datetime.now(timezone.utc).isoformat()')
        lines.append('        data["updated_at"] = data["created_at"]')
        if initial_state:
            lines.append(f'        data.setdefault("state", "{initial_state}")')
        lines.append('        self._store[data["id"]] = data')
        lines.append("        return data")
        lines.append("")
        lines.append("    async def update(self, id: str, data: dict) -> dict | None:")
        lines.append("        record = self._store.get(id)")
        lines.append("        if record is None:")
        lines.append("            return None")
        lines.append("        record.update(data)")
        lines.append('        record["updated_at"] = datetime.now(timezone.utc).isoformat()')
        lines.append("        return record")
        lines.append("")
        lines.append("    async def delete(self, id: str) -> bool:")
        lines.append("        if id in self._store:")
        lines.append("            del self._store[id]")
        lines.append("            return True")
        lines.append("        return False")
        lines.append("")

        if entity.state_machine:
            lines.append("    async def transition(self, id: str, new_state: str) -> dict | None:")
            lines.append("        record = self._store.get(id)")
            lines.append("        if record is None:")
            lines.append("            return None")
            lines.append('        current = record.get("state", "")')
            lines.append(f"        valid_transitions = {dict(transitions)}")
            lines.append("        if current not in valid_transitions or new_state not in valid_transitions[current]:")
            lines.append("            return None")
            lines.append('        record["state"] = new_state')
            lines.append('        record["updated_at"] = datetime.now(timezone.utc).isoformat()')
            lines.append("        return record")
            lines.append("")

        lines.append("")

    return GeneratedFile(
        path="backend/repositories/memory.py",
        content="\n".join(lines),
        provenance=fqns,
    )


def _generate_postgres(ir: DomainIR) -> GeneratedFile:
    fqns = ", ".join(e.fqn for e in ir.entities)
    header = provenance_header("python", fqns, "PostgreSQL repository adapters (asyncpg)")

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import json",
        "import uuid",
        "from datetime import datetime, timezone",
        "from typing import Any, Optional",
        "",
        "import asyncpg",
        "",
        "from backend.config import DATABASE_URL",
        "from backend.repositories.base import (",
        "    " + ",\n    ".join(f"{_to_class(e.name)}Repository" for e in ir.entities),
        ")",
        "",
        "",
        "# Connection pool — initialized on first use",
        "_pool: Optional[asyncpg.Pool] = None",
        "",
        "",
        "async def get_pool() -> asyncpg.Pool:",
        "    global _pool",
        "    if _pool is None:",
        "        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)",
        "    return _pool",
        "",
        "",
    ]

    for entity in ir.entities:
        cls = _to_class(entity.name)
        table = entity.table_name
        initial_state = entity.state_machine.initial if entity.state_machine else None
        transitions = entity.state_machine.transitions if entity.state_machine else {}

        # Get insertable fields (not computed except state)
        insert_fields = [f for f in entity.fields if not f.computed or f.name == "state"]
        field_names = [f.name for f in insert_fields]

        lines.append(f"class Postgres{cls}Repository({cls}Repository):")
        lines.append(f'    """PostgreSQL adapter for {entity.name}."""')
        lines.append("")

        # list
        lines.append("    async def list(self, limit: int = 100, offset: int = 0, filters: dict | None = None) -> tuple[list[dict], int]:")
        lines.append("        pool = await get_pool()")
        lines.append("        async with pool.acquire() as conn:")
        lines.append(f'            count = await conn.fetchval("SELECT COUNT(*) FROM {table}")')
        lines.append(f'            rows = await conn.fetch("SELECT * FROM {table} ORDER BY created_at DESC LIMIT $1 OFFSET $2", limit, offset)')
        lines.append("            return [dict(r) for r in rows], count")
        lines.append("")

        # get
        lines.append("    async def get(self, id: str) -> dict | None:")
        lines.append("        pool = await get_pool()")
        lines.append("        async with pool.acquire() as conn:")
        lines.append(f'            row = await conn.fetchrow("SELECT * FROM {table} WHERE id = $1", id)')
        lines.append("            return dict(row) if row else None")
        lines.append("")

        # create
        cols = ", ".join(field_names)
        placeholders = ", ".join(f"${i+1}" for i in range(len(field_names)))
        lines.append("    async def create(self, data: dict) -> dict:")
        lines.append("        pool = await get_pool()")
        lines.append('        if "id" not in data:')
        lines.append('            data["id"] = str(uuid.uuid4())')
        lines.append('        data["created_at"] = datetime.now(timezone.utc)')
        lines.append('        data["updated_at"] = data["created_at"]')
        if initial_state:
            lines.append(f'        data.setdefault("state", "{initial_state}")')
        lines.append("        async with pool.acquire() as conn:")
        lines.append(f'            row = await conn.fetchrow(')
        lines.append(f'                "INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING *",')
        lines.append(f'                {", ".join(f"data.get(\\"{f}\\")" for f in field_names)},')
        lines.append(f'            )')
        lines.append("            return dict(row)")
        lines.append("")

        # update
        lines.append("    async def update(self, id: str, data: dict) -> dict | None:")
        lines.append("        pool = await get_pool()")
        lines.append('        data["updated_at"] = datetime.now(timezone.utc)')
        lines.append("        set_clauses = []")
        lines.append("        values = []")
        lines.append("        for i, (k, v) in enumerate(data.items(), 1):")
        lines.append("            set_clauses.append(f'{k} = ${i}')")
        lines.append("            values.append(v)")
        lines.append("        values.append(id)")
        lines.append(f'        sql = f"UPDATE {table} SET {{\\", \\".join(set_clauses)}} WHERE id = ${{len(values)}} RETURNING *"')
        lines.append("        async with pool.acquire() as conn:")
        lines.append("            row = await conn.fetchrow(sql, *values)")
        lines.append("            return dict(row) if row else None")
        lines.append("")

        # delete
        lines.append("    async def delete(self, id: str) -> bool:")
        lines.append("        pool = await get_pool()")
        lines.append("        async with pool.acquire() as conn:")
        lines.append(f'            result = await conn.execute("DELETE FROM {table} WHERE id = $1", id)')
        lines.append('            return result == "DELETE 1"')
        lines.append("")

        # transition
        if entity.state_machine:
            lines.append("    async def transition(self, id: str, new_state: str) -> dict | None:")
            lines.append("        pool = await get_pool()")
            lines.append("        async with pool.acquire() as conn:")
            lines.append(f'            row = await conn.fetchrow("SELECT * FROM {table} WHERE id = $1", id)')
            lines.append("            if not row:")
            lines.append("                return None")
            lines.append('            current = row["state"]')
            lines.append(f"            valid = {dict(transitions)}")
            lines.append("            if current not in valid or new_state not in valid[current]:")
            lines.append("                return None")
            lines.append(f'            updated = await conn.fetchrow("UPDATE {table} SET state = $1, updated_at = $2 WHERE id = $3 RETURNING *", new_state, datetime.now(timezone.utc), id)')
            lines.append("            return dict(updated) if updated else None")
            lines.append("")

        lines.append("")

    return GeneratedFile(
        path="backend/repositories/postgres.py",
        content="\n".join(lines),
        provenance=fqns,
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_targets/test_fastapi_prod.py -v`

- [ ] **Step 5: Commit**

```bash
git add forge/targets/fastapi_prod/gen_repositories.py tests/test_targets/test_fastapi_prod.py
git commit -m "feat(#6/T2): repository generator — base interface, memory adapter, postgres adapter"
```

---

### Task 3: Improved Models Generator

**Files:**
- Create: `forge/targets/fastapi_prod/gen_models.py`

- [ ] **Step 1: Implement models generator**

This is the same as the existing models generator but with the `_links` fix applied and using the `links` alias pattern.

```python
# forge/targets/fastapi_prod/gen_models.py
"""Generate Pydantic models for request/response validation."""
from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, FieldIR
from forge.targets.base import GeneratedFile, provenance_header

PYTHON_TYPE_MAP = {
    "string": "str", "integer": "int", "number": "float", "boolean": "bool",
    "text": "str", "array": "list", "object": "dict", "datetime": "str",
    "date": "str", "uuid": "str", "email": "str",
}


def _to_class(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def generate_models(ir: DomainIR) -> GeneratedFile:
    """Generate backend/models.py with Pydantic models."""
    if not ir.entities:
        return GeneratedFile(path="backend/models.py", content="", provenance="")

    fqns = ", ".join(e.fqn for e in ir.entities)
    header = provenance_header("python", fqns, "Pydantic models for request/response validation")

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "from typing import Any, Optional",
        "",
        "from pydantic import BaseModel, Field",
        "",
        "",
    ]

    for entity in ir.entities:
        cls = _to_class(entity.name)

        # Create model — exclude computed and immutable fields
        lines.append(f"class {cls}Create(BaseModel):")
        lines.append(f'    """Create request for {entity.name}."""')
        create_fields = [f for f in entity.fields if not f.computed and not f.immutable]
        if create_fields:
            for field in create_fields:
                py_type = PYTHON_TYPE_MAP.get(field.type, "Any")
                if not field.required:
                    py_type = f"Optional[{py_type}]"
                default = " = None" if not field.required else ""
                lines.append(f"    {field.name}: {py_type}{default}")
        else:
            lines.append("    pass")
        lines.append("")
        lines.append("")

        # Update model — all fields optional
        lines.append(f"class {cls}Update(BaseModel):")
        lines.append(f'    """Update request for {entity.name}."""')
        update_fields = [f for f in entity.fields if not f.computed and not f.immutable and f.name != "id"]
        if update_fields:
            for field in update_fields:
                py_type = PYTHON_TYPE_MAP.get(field.type, "Any")
                lines.append(f"    {field.name}: Optional[{py_type}] = None")
        else:
            lines.append("    pass")
        lines.append("")
        lines.append("")

        # Response model — all fields
        lines.append(f"class {cls}Response(BaseModel):")
        lines.append(f'    """Response model for {entity.name}."""')
        for field in entity.fields:
            py_type = PYTHON_TYPE_MAP.get(field.type, "Any")
            if not field.required:
                py_type = f"Optional[{py_type}]"
            default = " = None" if not field.required else ""
            lines.append(f"    {field.name}: {py_type}{default}")
        lines.append("    links: dict[str, str] = Field(default_factory=dict, alias='_links')")
        lines.append("")
        lines.append("    model_config = {'populate_by_name': True}")
        lines.append("")
        lines.append("")

    return GeneratedFile(
        path="backend/models.py",
        content="\n".join(lines),
        provenance=fqns,
    )
```

- [ ] **Step 2: Run full suite**

Run: `python -m pytest tests/ -q`

- [ ] **Step 3: Commit**

```bash
git add forge/targets/fastapi_prod/gen_models.py
git commit -m "feat(#6/T3): models generator — Pydantic Create/Update/Response with _links alias"
```

---

### Task 4: Route Generator (Repository-backed)

**Files:**
- Create: `forge/targets/fastapi_prod/gen_routes.py`

- [ ] **Step 1: Implement route generator**

```python
# forge/targets/fastapi_prod/gen_routes.py
"""Generate FastAPI route handlers that call repositories."""
from __future__ import annotations

from forge.ir.model import DomainIR, EndpointIR, EntityIR, RouteIR
from forge.targets.base import GeneratedFile, provenance_header

PYTHON_TYPE_MAP = {
    "string": "str", "integer": "int", "number": "float", "boolean": "bool",
    "text": "str", "array": "list", "object": "dict", "datetime": "str",
    "date": "str", "uuid": "str", "email": "str",
}


def _to_class(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def generate_routes(ir: DomainIR) -> list[GeneratedFile]:
    """Generate one route module per Route contract."""
    entity_map = {e.fqn: e for e in ir.entities}
    # Check for auth
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    files = []

    for route in ir.routes:
        entity = entity_map.get(route.entity_fqn)
        files.append(_generate_route(route, entity, auth_infra))

    return files


def _generate_route(route: RouteIR, entity: EntityIR | None, auth_infra) -> GeneratedFile:
    header = provenance_header("python", route.fqn, f"API routes for {route.name}")

    entity_name = route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name
    cls = _to_class(entity_name)
    base_path = route.base_path or f"/{entity_name}s"

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "import uuid",
        "from datetime import datetime, timezone",
        "from typing import Any",
        "",
        "from fastapi import APIRouter, Depends, HTTPException",
        "",
        f"from backend.models import {cls}Create, {cls}Update, {cls}Response",
        f"from backend.repositories.base import {cls}Repository, get_{entity_name}_repo",
    ]

    if auth_infra:
        lines.append("from backend.auth.middleware import require_auth, require_role")

    lines.extend([
        "",
        f'router = APIRouter(prefix="{base_path}", tags=["{entity_name}"])',
        "",
        "",
    ])

    for endpoint in route.endpoints:
        lines.extend(_generate_endpoint(endpoint, entity_name, cls, base_path, entity, auth_infra))
        lines.append("")

    return GeneratedFile(
        path=f"backend/routes_{entity_name}.py",
        content="\n".join(lines),
        provenance=route.fqn,
    )


def _generate_endpoint(endpoint, entity_name, cls, base_path, entity, auth_infra) -> list[str]:
    lines = []
    method = endpoint.method.lower()
    path = endpoint.path
    repo_dep = f"repo: {cls}Repository = Depends(get_{entity_name}_repo)"
    auth_dep = ""
    if auth_infra:
        auth_dep = ", user = Depends(require_auth)"

    if method == "get" and path == "/":
        lines.append(f'@router.get("/")')
        lines.append(f"async def list_{entity_name}s(limit: int = 100, offset: int = 0, {repo_dep}{auth_dep}):")
        lines.append(f'    """List {entity_name}s."""')
        lines.append(f"    items, total = await repo.list(limit=limit, offset=offset)")
        lines.append(f"    return {{'items': items, 'total': total}}")
        return lines

    if method == "get" and "{id}" in path:
        lines.append(f'@router.get("/{{record_id}}")')
        lines.append(f"async def get_{entity_name}(record_id: str, {repo_dep}{auth_dep}):")
        lines.append(f'    """Get {entity_name} by ID."""')
        lines.append(f"    record = await repo.get(record_id)")
        lines.append(f"    if not record:")
        lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
        lines.append(f"    return record")
        return lines

    if method == "post" and path == "/":
        status = endpoint.response_status or 201
        lines.append(f'@router.post("/", status_code={status})')
        lines.append(f"async def create_{entity_name}(body: {cls}Create, {repo_dep}{auth_dep}):")
        lines.append(f'    """Create {entity_name}."""')
        lines.append(f"    data = body.model_dump(exclude_none=True)")
        for field_name, expr in endpoint.auto_fields.items():
            if "uuid" in expr.lower():
                lines.append(f'    data["{field_name}"] = str(uuid.uuid4())')
            elif "now" in expr.lower():
                lines.append(f'    data["{field_name}"] = datetime.now(timezone.utc).isoformat()')
        lines.append(f"    record = await repo.create(data)")
        lines.append(f'    record["_links"] = {{"self": f"{base_path}/{{record[\'id\']}}"}}}')
        lines.append(f"    return record")
        return lines

    if method == "patch" and "{id}" in path:
        lines.append(f'@router.patch("/{{record_id}}")')
        lines.append(f"async def update_{entity_name}(record_id: str, body: {cls}Update, {repo_dep}{auth_dep}):")
        lines.append(f'    """Update {entity_name}."""')
        lines.append(f"    data = body.model_dump(exclude_none=True)")
        lines.append(f"    record = await repo.update(record_id, data)")
        lines.append(f"    if not record:")
        lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
        lines.append(f"    return record")
        return lines

    if method == "delete" and "{id}" in path:
        lines.append(f'@router.delete("/{{record_id}}", status_code=204)')
        lines.append(f"async def delete_{entity_name}(record_id: str, {repo_dep}{auth_dep}):")
        lines.append(f'    """Delete {entity_name}."""')
        lines.append(f"    deleted = await repo.delete(record_id)")
        lines.append(f"    if not deleted:")
        lines.append(f'        raise HTTPException(404, detail={{"error": "not_found"}})')
        lines.append(f"    return None")
        return lines

    if method == "put" and "state" in path:
        lines.append(f'@router.put("/{{record_id}}/state")')
        lines.append(f"async def transition_{entity_name}(record_id: str, body: dict[str, Any], {repo_dep}{auth_dep}):")
        lines.append(f'    """Transition {entity_name} state."""')
        lines.append(f'    new_state = body.get("state")')
        lines.append(f"    if not new_state:")
        lines.append(f'        raise HTTPException(422, detail={{"error": "state required"}})')
        lines.append(f"    record = await repo.transition(record_id, new_state)")
        lines.append(f"    if not record:")
        lines.append(f'        raise HTTPException(422, detail={{"error": "invalid_transition"}})')
        lines.append(f"    return record")
        return lines

    # Fallback
    lines.append(f'@router.{method}("{path}")')
    lines.append(f"async def {method}_{entity_name}_{path.replace('/', '_').strip('_')}():")
    lines.append(f'    """{endpoint.summary}"""')
    lines.append(f'    return {{"message": "not implemented"}}')
    return lines
```

- [ ] **Step 2: Commit**

```bash
git add forge/targets/fastapi_prod/gen_routes.py
git commit -m "feat(#6/T4): route generator — repository-backed handlers with auth injection"
```

---

### Task 5: Auth Generator

**Files:**
- Create: `forge/targets/fastapi_prod/gen_auth.py`

- [ ] **Step 1: Implement auth generator**

```python
# forge/targets/fastapi_prod/gen_auth.py
"""Generate auth system from infra/auth contract."""
from __future__ import annotations

from forge.ir.model import DomainIR, InfraIR
from forge.targets.base import GeneratedFile, provenance_header


def generate_auth(ir: DomainIR) -> list[GeneratedFile]:
    """Generate auth files if an infra/auth contract exists."""
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    if not auth_infra:
        return []

    return [
        _generate_interface(auth_infra),
        _generate_jwt_provider(auth_infra),
        _generate_middleware(auth_infra),
    ]


def _generate_interface(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Auth provider interface")
    content = f"""{header}
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel


class AuthUser(BaseModel):
    id: str
    email: str
    role: str


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self, token: str) -> Optional[AuthUser]: ...

    @abstractmethod
    async def create_token(self, user_data: dict) -> str: ...

    @abstractmethod
    async def refresh_token(self, token: str) -> Optional[str]: ...
"""
    return GeneratedFile(path="backend/auth/interface.py", content=content, provenance=infra.fqn)


def _generate_jwt_provider(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Built-in JWT auth provider")
    content = f"""{header}
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.auth.interface import AuthProvider, AuthUser
from backend.config import AUTH_SECRET, AUTH_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


class JWTAuthProvider(AuthProvider):

    async def authenticate(self, token: str) -> Optional[AuthUser]:
        try:
            payload = jwt.decode(token, AUTH_SECRET, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            email = payload.get("email", "")
            role = payload.get("role", "")
            if user_id is None:
                return None
            return AuthUser(id=user_id, email=email, role=role)
        except JWTError:
            return None

    async def create_token(self, user_data: dict) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=AUTH_TOKEN_EXPIRE_MINUTES)
        to_encode = {{
            "sub": user_data.get("id", ""),
            "email": user_data.get("email", ""),
            "role": user_data.get("role", ""),
            "exp": expire,
        }}
        return jwt.encode(to_encode, AUTH_SECRET, algorithm=ALGORITHM)

    async def refresh_token(self, token: str) -> Optional[str]:
        user = await self.authenticate(token)
        if user is None:
            return None
        return await self.create_token(user.model_dump())


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)
"""
    return GeneratedFile(path="backend/auth/jwt_provider.py", content=content, provenance=infra.fqn)


def _generate_middleware(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Auth middleware — FastAPI dependencies")
    roles = infra.config.get("roles", [])

    content = f"""{header}
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Header

from backend.auth.interface import AuthProvider, AuthUser
from backend.auth.jwt_provider import JWTAuthProvider
from backend.config import AUTH_ENABLED

_provider: Optional[AuthProvider] = None


def get_auth_provider() -> AuthProvider:
    global _provider
    if _provider is None:
        _provider = JWTAuthProvider()
    return _provider


async def require_auth(
    authorization: str = Header(None),
    provider: AuthProvider = Depends(get_auth_provider),
) -> AuthUser:
    if not AUTH_ENABLED:
        return AuthUser(id="anonymous", email="", role="admin")
    if not authorization:
        raise HTTPException(401, detail={{"error": "missing_token"}})
    token = authorization.replace("Bearer ", "")
    user = await provider.authenticate(token)
    if user is None:
        raise HTTPException(401, detail={{"error": "invalid_token"}})
    return user


def require_role(*roles: str):
    async def check(user: AuthUser = Depends(require_auth)) -> AuthUser:
        if not AUTH_ENABLED:
            return user
        if user.role not in roles:
            raise HTTPException(403, detail={{"error": "forbidden", "required_roles": list(roles)}})
        return user
    return check
"""
    return GeneratedFile(path="backend/auth/middleware.py", content=content, provenance=infra.fqn)
```

- [ ] **Step 2: Commit**

```bash
git add forge/targets/fastapi_prod/gen_auth.py
git commit -m "feat(#6/T5): auth generator — JWT provider, middleware, pluggable interface"
```

---

### Task 6: App Generator + Docker Generator

**Files:**
- Create: `forge/targets/fastapi_prod/gen_app.py`
- Create: `forge/targets/fastapi_prod/gen_docker.py`

- [ ] **Step 1: Implement app generator**

```python
# forge/targets/fastapi_prod/gen_app.py
"""Generate FastAPI application entrypoint with middleware stack."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile, provenance_header


def generate_app(ir: DomainIR) -> GeneratedFile:
    header = provenance_header("python", f"domain/{ir.domain}", "FastAPI application entrypoint")
    has_auth = any(i.category == "auth" for i in ir.infra)

    route_imports = []
    route_includes = []
    for route in ir.routes:
        entity_name = route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name
        module = f"routes_{entity_name}"
        route_imports.append(f"from backend.{module} import router as {entity_name}_router")
        route_includes.append(f"app.include_router({entity_name}_router)")

    lines = [
        header,
        "from fastapi import FastAPI",
        "from fastapi.middleware.cors import CORSMiddleware",
        "",
        "from backend.config import CORS_ORIGINS, PORT",
        *route_imports,
        "",
        f'app = FastAPI(title="Specora Generated API — {ir.domain}")',
        "",
        "# CORS",
        "app.add_middleware(",
        "    CORSMiddleware,",
        "    allow_origins=CORS_ORIGINS,",
        '    allow_credentials=True,',
        '    allow_methods=["*"],',
        '    allow_headers=["*"],',
        ")",
        "",
        *route_includes,
        "",
        "",
        '@app.get("/health")',
        "async def health():",
        f'    return {{"status": "ok", "domain": "{ir.domain}"}}',
        "",
    ]

    if has_auth:
        lines.insert(lines.index("from backend.config import CORS_ORIGINS, PORT") + 1,
                     "from backend.auth.jwt_provider import JWTAuthProvider, verify_password")
        lines.extend([
            "",
            '@app.post("/auth/login")',
            "async def login(body: dict):",
            '    """Authenticate and return a JWT token."""',
            "    provider = JWTAuthProvider()",
            '    token = await provider.create_token({"id": body.get("id", ""), "email": body.get("email", ""), "role": body.get("role", "user")})',
            '    return {"access_token": token, "token_type": "bearer"}',
            "",
        ])

    return GeneratedFile(
        path="backend/app.py",
        content="\n".join(lines),
        provenance=f"domain/{ir.domain}",
    )
```

- [ ] **Step 2: Implement Docker generator**

```python
# forge/targets/fastapi_prod/gen_docker.py
"""Generate Dockerfile, docker-compose.yml, .env.example, requirements.txt."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile, provenance_header


def generate_docker(ir: DomainIR) -> list[GeneratedFile]:
    has_auth = any(i.category == "auth" for i in ir.infra)
    return [
        _generate_dockerfile(ir),
        _generate_compose(ir),
        _generate_env_example(ir, has_auth),
        _generate_requirements(ir, has_auth),
    ]


def _generate_dockerfile(ir: DomainIR) -> GeneratedFile:
    header = provenance_header("yaml", f"domain/{ir.domain}", "Docker build")
    content = f"""FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY database/ database/
EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
"""
    return GeneratedFile(path="Dockerfile", content=content, provenance=f"domain/{ir.domain}")


def _generate_compose(ir: DomainIR) -> GeneratedFile:
    content = f"""# @generated from domain/{ir.domain}
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: specora
      POSTGRES_USER: specora
      POSTGRES_PASSWORD: specora
    volumes:
      - ./database/schema.sql:/docker-entrypoint-initdb.d/001_schema.sql
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U specora"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://specora:specora@db:5432/specora
      DATABASE_BACKEND: postgres
      AUTH_ENABLED: "${{AUTH_ENABLED:-false}}"
      AUTH_SECRET: "${{AUTH_SECRET:-change-me}}"
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
"""
    return GeneratedFile(path="docker-compose.yml", content=content, provenance=f"domain/{ir.domain}")


def _generate_env_example(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    lines = [
        f"# Environment variables for {ir.domain}",
        "# Copy to .env and customize",
        "",
        "# Database",
        "DATABASE_URL=postgresql://specora:specora@localhost:5432/specora",
        "DATABASE_BACKEND=postgres  # postgres | memory",
        "",
        "# Server",
        "PORT=8000",
        "CORS_ORIGINS=*",
    ]
    if has_auth:
        lines.extend([
            "",
            "# Auth",
            "AUTH_ENABLED=true",
            "AUTH_PROVIDER=jwt",
            "AUTH_SECRET=change-me-in-production",
            "AUTH_TOKEN_EXPIRE_MINUTES=60",
        ])
    lines.append("")
    return GeneratedFile(path=".env.example", content="\n".join(lines), provenance=f"domain/{ir.domain}")


def _generate_requirements(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    deps = [
        "fastapi>=0.110",
        "uvicorn>=0.29",
        "pydantic>=2.0",
        "asyncpg>=0.29",
        "httpx>=0.27",
    ]
    if has_auth:
        deps.extend([
            "python-jose[cryptography]>=3.3",
            "passlib[bcrypt]>=1.7",
            "python-multipart>=0.0.9",
        ])
    return GeneratedFile(path="requirements.txt", content="\n".join(deps) + "\n", provenance=f"domain/{ir.domain}")
```

- [ ] **Step 3: Commit**

```bash
git add forge/targets/fastapi_prod/gen_app.py forge/targets/fastapi_prod/gen_docker.py
git commit -m "feat(#6/T6): app generator + Docker generator — compose, Dockerfile, requirements"
```

---

### Task 7: Test Generator

**Files:**
- Create: `forge/targets/fastapi_prod/gen_tests.py`

- [ ] **Step 1: Implement test generator**

```python
# forge/targets/fastapi_prod/gen_tests.py
"""Generate black-box pytest tests from Route contracts."""
from __future__ import annotations

import json

from forge.ir.model import DomainIR, EntityIR, RouteIR
from forge.targets.base import GeneratedFile, provenance_header


def _to_class(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def generate_tests(ir: DomainIR) -> list[GeneratedFile]:
    """Generate test files for each route."""
    if not ir.routes:
        return []

    entity_map = {e.fqn: e for e in ir.entities}
    has_auth = any(i.category == "auth" for i in ir.infra)
    files = [_generate_conftest(ir, has_auth)]

    for route in ir.routes:
        entity = entity_map.get(route.entity_fqn)
        if entity:
            files.append(_generate_entity_tests(route, entity, has_auth))

    return files


def _generate_conftest(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    header = provenance_header("python", f"domain/{ir.domain}", "Test fixtures")

    lines = [
        header,
        "import pytest",
        "from fastapi.testclient import TestClient",
        "",
        "from backend.app import app",
        "from backend.config import DATABASE_BACKEND",
        "",
        "",
        "@pytest.fixture",
        "def client():",
        '    """Test client using in-memory backend."""',
        "    return TestClient(app)",
        "",
    ]

    if has_auth:
        lines.extend([
            "",
            "@pytest.fixture",
            "def auth_headers(client):",
            '    """Get auth headers for testing."""',
            '    resp = client.post("/auth/login", json={"id": "test-user", "email": "test@test.com", "role": "admin"})',
            '    token = resp.json()["access_token"]',
            '    return {"Authorization": f"Bearer {token}"}',
            "",
        ])

    return GeneratedFile(path="tests/conftest.py", content="\n".join(lines), provenance=f"domain/{ir.domain}")


def _generate_entity_tests(route: RouteIR, entity: EntityIR, has_auth: bool) -> GeneratedFile:
    header = provenance_header("python", route.fqn, f"Tests for {route.name}")
    entity_name = entity.name
    cls = _to_class(entity_name)
    base_path = route.base_path or f"/{entity_name}s"
    auth_arg = ", auth_headers" if has_auth else ""
    headers_kwarg = "headers=auth_headers" if has_auth else ""

    # Build a sample create payload from required fields
    sample_data = {}
    for f in entity.fields:
        if f.computed or f.immutable:
            continue
        if f.required:
            if f.enum_values:
                sample_data[f.name] = f.enum_values[0]
            elif f.type == "string":
                sample_data[f.name] = f"test_{f.name}"
            elif f.type == "email":
                sample_data[f.name] = f"test@example.com"
            elif f.type == "integer":
                sample_data[f.name] = 1
            elif f.type == "number":
                sample_data[f.name] = 1.0
            elif f.type == "boolean":
                sample_data[f.name] = True
            elif f.type == "uuid":
                sample_data[f.name] = "00000000-0000-0000-0000-000000000001"
            elif f.type == "text":
                sample_data[f.name] = f"Test {f.name} content"
            else:
                sample_data[f.name] = f"test"
    if entity.state_machine:
        sample_data["state"] = entity.state_machine.initial

    sample_json = json.dumps(sample_data, indent=4)

    lines = [
        header,
        "import pytest",
        "",
        "",
        f"SAMPLE_DATA = {sample_json}",
        "",
        "",
        f"class Test{cls}CRUD:",
        "",
        f"    def test_create(self, client{auth_arg}):",
        f'        resp = client.post("{base_path}/", json=SAMPLE_DATA, {headers_kwarg})',
        f"        assert resp.status_code == 201",
        f"        data = resp.json()",
        f'        assert "id" in data',
    ]

    # Check required fields in response
    for f in entity.fields:
        if f.required and not f.computed and f.name in sample_data:
            lines.append(f'        assert data["{f.name}"] == SAMPLE_DATA["{f.name}"]')

    lines.extend([
        "",
        f"    def test_list(self, client{auth_arg}):",
        f'        resp = client.get("{base_path}/", {headers_kwarg})',
        f"        assert resp.status_code == 200",
        f'        assert "items" in resp.json()',
        "",
        f"    def test_get_by_id(self, client{auth_arg}):",
        f'        created = client.post("{base_path}/", json=SAMPLE_DATA, {headers_kwarg}).json()',
        f'        resp = client.get(f"{base_path}/{{created[\'id\']}}", {headers_kwarg})',
        f"        assert resp.status_code == 200",
        "",
        f"    def test_get_nonexistent_returns_404(self, client{auth_arg}):",
        f'        resp = client.get("{base_path}/nonexistent", {headers_kwarg})',
        f"        assert resp.status_code == 404",
        "",
        f"    def test_delete(self, client{auth_arg}):",
        f'        created = client.post("{base_path}/", json=SAMPLE_DATA, {headers_kwarg}).json()',
        f'        resp = client.delete(f"{base_path}/{{created[\'id\']}}", {headers_kwarg})',
        f"        assert resp.status_code == 204",
        "",
    ])

    # State transition tests if entity has workflow
    if entity.state_machine:
        initial = entity.state_machine.initial
        transitions = entity.state_machine.transitions
        if initial in transitions and transitions[initial]:
            next_state = transitions[initial][0]
            lines.extend([
                f"    def test_state_transition(self, client{auth_arg}):",
                f'        created = client.post("{base_path}/", json=SAMPLE_DATA, {headers_kwarg}).json()',
                f'        resp = client.put(f"{base_path}/{{created[\'id\']}}/state", json={{"state": "{next_state}"}}, {headers_kwarg})',
                f"        assert resp.status_code == 200",
                f'        assert resp.json()["state"] == "{next_state}"',
                "",
                f"    def test_invalid_transition(self, client{auth_arg}):",
                f'        created = client.post("{base_path}/", json=SAMPLE_DATA, {headers_kwarg}).json()',
                f'        resp = client.put(f"{base_path}/{{created[\'id\']}}/state", json={{"state": "nonexistent_state"}}, {headers_kwarg})',
                f"        assert resp.status_code == 422",
                "",
            ])

    return GeneratedFile(
        path=f"tests/test_{entity_name}.py",
        content="\n".join(lines),
        provenance=route.fqn,
    )
```

- [ ] **Step 2: Commit**

```bash
git add forge/targets/fastapi_prod/gen_tests.py
git commit -m "feat(#6/T7): test generator — black-box pytest tests from Route contracts"
```

---

### Task 8: Main Generator + Registration

**Files:**
- Create: `forge/targets/fastapi_prod/generator.py`
- Modify: `forge/cli/main.py` — register `fastapi-prod`, `docker`, `tests` targets

- [ ] **Step 1: Implement the orchestrating generator**

```python
# forge/targets/fastapi_prod/generator.py
"""Production FastAPI generator — orchestrates all sub-generators."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import BaseGenerator, GeneratedFile
from forge.targets.fastapi_prod.gen_app import generate_app
from forge.targets.fastapi_prod.gen_auth import generate_auth
from forge.targets.fastapi_prod.gen_config import generate_config
from forge.targets.fastapi_prod.gen_docker import generate_docker
from forge.targets.fastapi_prod.gen_models import generate_models
from forge.targets.fastapi_prod.gen_repositories import generate_repositories
from forge.targets.fastapi_prod.gen_routes import generate_routes
from forge.targets.fastapi_prod.gen_tests import generate_tests


class FastAPIProductionGenerator(BaseGenerator):
    """Production-grade FastAPI generator with repos, auth, Docker, tests."""

    def name(self) -> str:
        return "fastapi-prod"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        files: list[GeneratedFile] = []

        # Config
        files.append(generate_config(ir))

        # Models
        models = generate_models(ir)
        if models.content:
            files.append(models)

        # Repositories
        files.extend(generate_repositories(ir))

        # Auth (only if infra/auth contract exists)
        files.extend(generate_auth(ir))

        # Routes
        files.extend(generate_routes(ir))

        # App
        files.append(generate_app(ir))

        return files


class DockerGenerator(BaseGenerator):
    """Generates Docker deployment files."""

    def name(self) -> str:
        return "docker"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        return generate_docker(ir)


class TestSuiteGenerator(BaseGenerator):
    """Generates black-box pytest tests."""

    def name(self) -> str:
        return "tests"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        return generate_tests(ir)
```

- [ ] **Step 2: Register new generators in CLI**

In `forge/cli/main.py`, read the `_get_generators` function and add the new generators to the registry dict:

```python
from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator, DockerGenerator, TestSuiteGenerator

registry = {
    "typescript": TypeScriptGenerator,
    "fastapi": FastAPIGenerator,
    "postgres": PostgresGenerator,
    "fastapi-prod": FastAPIProductionGenerator,
    "docker": DockerGenerator,
    "tests": TestSuiteGenerator,
}
```

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`

- [ ] **Step 4: End-to-end test — generate and boot**

```bash
# Generate production app
python -m forge.cli.main forge generate domains/task_manager --target fastapi-prod --target postgres --target docker --target tests --output runtime/

# Verify files exist
ls runtime/backend/repositories/
ls runtime/backend/auth/ 2>/dev/null || echo "No auth (expected — no infra/auth contract yet)"
ls runtime/tests/
cat runtime/Dockerfile
cat runtime/docker-compose.yml

# Boot with memory backend and test
cd runtime/
pip install -r requirements.txt
DATABASE_BACKEND=memory python -m uvicorn backend.app:app --port 9000 &
sleep 2
curl -s http://localhost:9000/health
curl -s -X POST http://localhost:9000/tasks/ -H "Content-Type: application/json" -d '{"title":"Test","priority":"high","project_id":"test-proj"}'
curl -s http://localhost:9000/tasks/
kill %1
cd ..

# Run generated tests
cd runtime/
DATABASE_BACKEND=memory pytest tests/ -v
cd ..
```

- [ ] **Step 5: Commit**

```bash
git add forge/targets/fastapi_prod/generator.py forge/cli/main.py
git commit -m "feat(#6/T8): production generator orchestrator + CLI registration"
```

---

## Verification Checklist

- [ ] `python -m pytest tests/ -v` — all specora-core tests pass
- [ ] `spc forge generate domains/task_manager --target fastapi-prod --target postgres --target docker --target tests` — generates all files
- [ ] Generated app boots with `DATABASE_BACKEND=memory uvicorn backend.app:app`
- [ ] `curl localhost:8000/health` returns ok
- [ ] CRUD endpoints work (create, list, get, update, delete)
- [ ] State transitions work (backlog → todo → in_progress)
- [ ] Generated tests pass: `cd runtime && DATABASE_BACKEND=memory pytest tests/ -v`
- [ ] `docker compose up` boots app + Postgres (if Docker available)
- [ ] Adding an `infra/auth` contract and regenerating produces auth middleware

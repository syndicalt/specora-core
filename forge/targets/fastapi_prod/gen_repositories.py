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

        # create — dynamic INSERT from data keys
        lines.append("    async def create(self, data: dict) -> dict:")
        lines.append("        pool = await get_pool()")
        lines.append('        if "id" not in data:')
        lines.append('            data["id"] = str(uuid.uuid4())')
        lines.append('        data["created_at"] = datetime.now(timezone.utc)')
        lines.append('        data["updated_at"] = data["created_at"]')
        if initial_state:
            lines.append(f'        data.setdefault("state", "{initial_state}")')
        lines.append("        cols = list(data.keys())")
        lines.append("        vals = list(data.values())")
        lines.append('        col_str = ", ".join(cols)')
        lines.append('        ph_str = ", ".join(f"${i+1}" for i in range(len(cols)))')
        lines.append(f'        sql = f"INSERT INTO {table} ({{col_str}}) VALUES ({{ph_str}}) RETURNING *"')
        lines.append("        async with pool.acquire() as conn:")
        lines.append("            row = await conn.fetchrow(sql, *vals)")
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
        lines.append(f'        sql = f"UPDATE {table} SET ' + '{", ".join(set_clauses)}' + f' WHERE id = $' + '{len(values)}' + f' RETURNING *"')
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

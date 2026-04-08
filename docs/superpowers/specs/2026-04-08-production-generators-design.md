# Production-Grade Code Generation — Design Spec

**Date:** 2026-04-08
**Status:** Approved
**Issue:** syndicalt/specora-core#6

## Purpose

Replace the in-memory store generators with production-grade code generation. The same contracts that work today should produce a complete, bootable, deployable application with real database persistence, authentication, Docker deployment, and contract-derived test suites.

**Key principle:** The contract doesn't change. The generator target changes. `spc forge generate --target postgres` produces a Postgres-backed app. `--target sqlite` produces a SQLite-backed app. Same contracts, different infrastructure.

## Architecture: Repository Pattern

Routes call an abstract repository interface, never a database directly. Concrete adapters implement the interface for each backend.

```
Route Handler
  → depends on Repository Interface (abstract)
    → PostgresRepository (asyncpg)
    → MemoryRepository (dict, for dev/testing)
    → SQLiteRepository (future)
    → MongoRepository (future)
```

### Generated File Structure

```
runtime/
├── backend/
│   ├── app.py                    # FastAPI app with middleware stack
│   ├── config.py                 # 12-factor env configuration
│   ├── models.py                 # Pydantic models (existing, improved)
│   ├── repositories/
│   │   ├── base.py               # Abstract repository per entity
│   │   ├── postgres.py           # PostgreSQL adapter (asyncpg)
│   │   └── memory.py             # In-memory adapter (dev/test)
│   ├── auth/
│   │   ├── interface.py          # Abstract AuthProvider
│   │   ├── jwt_provider.py       # Built-in JWT (login, refresh, validate)
│   │   ├── middleware.py         # Depends(require_role("admin"))
│   │   └── external.py           # Auth0/Keycloak stub
│   ├── routes_{entity}.py        # Route handlers (call repo, not DB)
│   └── migrations/
│       └── 001_initial.sql       # Versioned schema
├── tests/
│   ├── conftest.py               # Fixtures: TestClient, test DB, auth tokens
│   └── test_{entity}.py          # Black-box API tests
├── database/
│   └── schema.sql                # Full DDL
├── Dockerfile
├── docker-compose.yml            # App + Postgres + optional Healer
├── .env.example                  # All env vars documented
├── requirements.txt
└── types.ts
```

## Component Details

### 1. Repository Interface (`repositories/base.py`)

Generated per entity from EntityIR:

```python
class TaskRepository(ABC):
    @abstractmethod
    async def list(self, limit: int = 100, offset: int = 0, filters: dict | None = None) -> tuple[list[dict], int]: ...
    
    @abstractmethod
    async def get(self, id: str) -> dict | None: ...
    
    @abstractmethod
    async def create(self, data: dict) -> dict: ...
    
    @abstractmethod
    async def update(self, id: str, data: dict) -> dict | None: ...
    
    @abstractmethod
    async def delete(self, id: str) -> bool: ...
```

If the entity has a state machine, the interface also includes:
```python
    @abstractmethod
    async def transition(self, id: str, new_state: str) -> dict | None: ...
```

### 2. PostgreSQL Adapter (`repositories/postgres.py`)

Uses `asyncpg` for async Postgres access. Generated from EntityIR + the schema.sql:

- `list()` → `SELECT * FROM {table} LIMIT $1 OFFSET $2` with optional WHERE filters
- `get()` → `SELECT * FROM {table} WHERE id = $1`
- `create()` → `INSERT INTO {table} (...) VALUES (...) RETURNING *`
- `update()` → `UPDATE {table} SET ... WHERE id = $1 RETURNING *`
- `delete()` → `DELETE FROM {table} WHERE id = $1`
- `transition()` → validates against StateMachineIR transitions, checks guards, executes side effects

Connection pool initialized from `DATABASE_URL` env var.

### 3. Memory Adapter (`repositories/memory.py`)

The current in-memory dict pattern, extracted into the repository interface. Used for:
- Development without a database
- Test fixtures (fast, no setup)
- Demo mode

### 4. Config (`config.py`)

12-factor configuration from environment variables:

```python
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://specora:specora@localhost:5432/specora")
DATABASE_BACKEND = os.getenv("DATABASE_BACKEND", "postgres")  # "postgres" | "memory" | "sqlite"
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() in ("true", "1")
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "jwt")  # "jwt" | "external"
AUTH_SECRET = os.getenv("AUTH_SECRET", "change-me-in-production")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
PORT = int(os.getenv("PORT", "8000"))
```

### 5. Auth System

**Generated from `infra/auth` contract** (if present in the domain). If no auth contract exists, auth middleware is not generated.

**infra/auth contract example:**
```yaml
apiVersion: specora.dev/v1
kind: Infra
metadata:
  name: auth
  domain: task_manager
spec:
  category: auth
  config:
    provider: jwt
    roles: [admin, manager, member, viewer]
    protected_routes:
      - path: /users
        methods: [POST, PATCH, DELETE]
        roles: [admin]
      - path: /tasks
        methods: [POST, PATCH, DELETE]
        roles: [admin, manager, member]
      - path: /tasks
        methods: [GET]
        roles: [admin, manager, member, viewer]
```

**Generated components:**
- `auth/interface.py` — `AuthProvider` ABC with `authenticate(token) -> User`, `create_token(user) -> str`
- `auth/jwt_provider.py` — Built-in JWT: `POST /auth/login` (email+password), `POST /auth/refresh`, token validation
- `auth/middleware.py` — `require_auth()` and `require_role("admin")` as FastAPI dependencies
- `auth/external.py` — Stub for Auth0/Keycloak integration (reads JWKS from external provider)

### 6. Route Handlers (Improved)

The current route generator is replaced. Key changes:
- Routes call `repo.create(data)` instead of `_store[id] = data`
- Auth dependencies injected where the auth contract specifies protection
- Proper error responses with consistent error format
- State transition endpoint validates against the workflow
- Pagination with `Link` headers

```python
@router.post("/", status_code=201)
async def create_task(
    body: TaskCreate,
    repo: TaskRepository = Depends(get_task_repo),
    user: User = Depends(require_role("member")),
):
    data = body.model_dump(exclude_none=True)
    data["id"] = str(uuid.uuid4())
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    data["created_by"] = user.id
    record = await repo.create(data)
    return record
```

### 7. Generated Tests (`tests/`)

Black-box pytest tests generated from Route contracts:

```python
# test_task.py (generated)
class TestTaskCRUD:
    def test_create_task(self, client, auth_token):
        resp = client.post("/tasks/", json={"title": "Test", "priority": "high", "project_id": "..."}, headers=auth_token)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test"
        assert "id" in data

    def test_list_tasks(self, client, auth_token):
        resp = client.get("/tasks/", headers=auth_token)
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_get_nonexistent_returns_404(self, client, auth_token):
        resp = client.get("/tasks/nonexistent", headers=auth_token)
        assert resp.status_code == 404

    def test_state_transition(self, client, auth_token):
        # Create task (starts in backlog)
        task = client.post("/tasks/", json={...}, headers=auth_token).json()
        # Transition to todo
        resp = client.put(f"/tasks/{task['id']}/state", json={"state": "todo"}, headers=auth_token)
        assert resp.status_code == 200
        assert resp.json()["state"] == "todo"

    def test_invalid_transition_rejected(self, client, auth_token):
        task = client.post("/tasks/", json={...}, headers=auth_token).json()
        # Can't go from backlog to done
        resp = client.put(f"/tasks/{task['id']}/state", json={"state": "done"}, headers=auth_token)
        assert resp.status_code == 422
```

### 8. Docker

**Dockerfile:**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY database/ database/
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**docker-compose.yml:**
```yaml
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
    ports: ["5432:5432"]

  app:
    build: .
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql://specora:specora@db:5432/specora
      DATABASE_BACKEND: postgres
      AUTH_ENABLED: "true"
      AUTH_SECRET: "${AUTH_SECRET:-change-me}"
    depends_on: [db]

volumes:
  pgdata:
```

## New Generators

| Generator | Target flag | What it produces |
|-----------|------------|------------------|
| `FastAPIProductionGenerator` | `fastapi-prod` | app.py, config.py, models.py, repositories/, auth/, routes, migrations |
| `DockerGenerator` | `docker` | Dockerfile, docker-compose.yml, .env.example, requirements.txt |
| `TestGenerator` | `tests` | conftest.py, test_{entity}.py per entity |

The existing generators (`typescript`, `fastapi`, `postgres`) remain for backward compatibility. The new `fastapi-prod` generator replaces `fastapi` for production use.

## Dependencies on Existing Modules

| Module | Used For |
|--------|----------|
| `forge.ir.model` | DomainIR, EntityIR, RouteIR, StateMachineIR, InfraIR |
| `forge.targets.base` | BaseGenerator, GeneratedFile, provenance_header |
| `forge.targets.postgres.gen_ddl` | Reuse DDL generation logic for migrations |

## Generated App Dependencies

```
fastapi>=0.110
uvicorn>=0.29
pydantic>=2.0
asyncpg>=0.29
python-jose[cryptography]>=3.3
passlib[bcrypt]>=1.7
python-multipart>=0.0.9
httpx>=0.27
pytest>=8.0
pytest-asyncio>=0.23
```

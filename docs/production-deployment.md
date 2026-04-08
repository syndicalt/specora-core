# Production Deployment

> **Note**: The primary interface for Specora Core is your LLM coding agent (Claude Code, Cursor, Windsurf). The LLM calls Python generator functions directly. The CLI commands shown below are the equivalent for CI/CD pipelines and terminal users.

This guide covers the complete workflow from contracts to a running production application: generating production-grade FastAPI code, Docker deployment with Healer sidecar, authentication, database backends, and environment configuration.

---

## Overview

Specora Core includes three production-oriented generators:

| Generator | Target name | What it produces |
|-----------|------------|------------------|
| FastAPI Production | `fastapi-prod` | Config, Pydantic models, repository interfaces + adapters, route handlers, app entrypoint, auth system |
| Docker | `docker` | Dockerfile, docker-compose.yml, .env.example, requirements.txt |
| Tests | `tests` | Black-box pytest tests (stub -- full implementation pending) |

These generators work together. The `fastapi-prod` target generates the application code. The `docker` target wraps it in containers. The `tests` target generates tests to verify the running API.

---

## Step-by-Step: Contracts to Running API

### 1. Write your contracts

```
domains/inventory/
  entities/
    product.contract.yaml
    warehouse.contract.yaml
  workflows/
    product_lifecycle.contract.yaml
  routes/
    products.contract.yaml
    warehouses.contract.yaml
  infra/
    auth.contract.yaml          # optional -- include for JWT auth
```

### 2. Validate

**Python API** (what the LLM uses):

```python
from pathlib import Path
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all

contracts = load_all_contracts(Path("domains/inventory"))
errors = validate_all(contracts)
assert not errors, f"Validation failed: {errors}"
print(f"{len(contracts)} contracts loaded, 0 errors")
```

**CLI equivalent:**

```bash
spc forge validate domains/inventory
```

Expected output:

```
Validating domains/inventory...
  entity/inventory/product        OK
  entity/inventory/warehouse      OK
  workflow/inventory/product_lifecycle  OK
  route/inventory/products        OK
  route/inventory/warehouses      OK
  infra/inventory/auth            OK

6 contracts validated, 0 errors, 0 warnings
```

### 3. Generate production code

**Python API** (what the LLM uses):

```python
from forge.ir.compiler import Compiler
from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator
from forge.targets.postgres.gen_ddl import PostgresGenerator
from forge.targets.fastapi_prod.gen_docker import generate_docker

ir = Compiler(contract_root=Path("domains/inventory")).compile()
output = Path("output/")

for gen in [FastAPIProductionGenerator(), PostgresGenerator()]:
    for f in gen.generate(ir):
        p = output / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f.content, encoding="utf-8")

for f in generate_docker(ir):
    p = output / f.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f.content, encoding="utf-8")
```

**CLI equivalent:**

```bash
spc forge generate domains/inventory --target fastapi-prod --output output/
spc forge generate domains/inventory --target docker --output output/
```

### 4. Review generated files

After generation, your output directory contains:

```
output/
  backend/
    config.py                   # Environment-based configuration
    models.py                   # Pydantic Create/Update/Response models
    app.py                      # FastAPI entrypoint with CORS + routers
    routes_product.py           # Product API routes
    routes_warehouse.py         # Warehouse API routes
    auth/                       # Only if infra/auth contract exists
      interface.py              # Abstract AuthProvider
      jwt_provider.py           # JWT implementation
      middleware.py             # require_auth, require_role
    repositories/
      base.py                   # Abstract interfaces + factory functions
      memory.py                 # In-memory adapters (dev/test)
      postgres.py               # PostgreSQL adapters (asyncpg)
  database/
    schema.sql                  # CREATE TABLE DDL
  Dockerfile
  docker-compose.yml
  .env.example
  requirements.txt
```

### 5. Set up environment

```bash
cp output/.env.example output/.env
# Edit .env with your values
```

### 6. Run with Docker Compose

```bash
cd output/
docker compose up -d
```

This starts:
- **PostgreSQL 16** on port 5432 (with schema auto-applied via init script)
- **FastAPI app** on port 8000 (waits for Postgres health check)

### 7. Verify

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "ok", "domain": "inventory"}
```

```bash
curl http://localhost:8000/products
```

Expected response:

```json
{"items": [], "total": 0}
```

---

## The `fastapi-prod` Generator

The production FastAPI generator (`forge/targets/fastapi_prod/generator.py`) orchestrates seven sub-generators:

### `gen_config.py` -- Configuration Module

Generates `backend/config.py` with 12-factor environment-based configuration:

```python
import os

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://specora:specora@localhost:5432/specora")
DATABASE_BACKEND = os.getenv("DATABASE_BACKEND", "postgres")

# Server
PORT = int(os.getenv("PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# Auth
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() in ("true", "1")
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "jwt")
AUTH_SECRET = os.getenv("AUTH_SECRET", "change-me-in-production")
AUTH_TOKEN_EXPIRE_MINUTES = int(os.getenv("AUTH_TOKEN_EXPIRE_MINUTES", "60"))
```

If the domain includes an `infra/auth` contract, `AUTH_ENABLED` defaults to `"true"`.

### `gen_models.py` -- Pydantic Models

For each entity, generates three Pydantic models:

- **`{Entity}Create`** -- Request body for POST. Excludes computed and immutable fields.
- **`{Entity}Update`** -- Request body for PATCH. All fields optional.
- **`{Entity}Response`** -- Response body. All fields included, plus `_links` for HATEOAS.

Example for a `product` entity:

```python
class ProductCreate(BaseModel):
    """Create request for product."""
    name: str
    sku: str
    price: Optional[float] = None

class ProductUpdate(BaseModel):
    """Update request for product."""
    name: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None

class ProductResponse(BaseModel):
    """Response model for product."""
    id: str
    name: str
    sku: str
    price: Optional[float] = None
    state: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    links: dict[str, str] = Field(default_factory=dict, alias='_links')

    model_config = {'populate_by_name': True}
```

### `gen_repositories.py` -- Repository Pattern

Generates three files implementing the repository pattern:

#### `backend/repositories/base.py` -- Abstract Interfaces

One abstract class per entity with standard CRUD operations:

```python
class ProductRepository(ABC):
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

    @abstractmethod
    async def transition(self, id: str, new_state: str) -> dict | None: ...  # if entity has state machine
```

Plus factory functions that read `DATABASE_BACKEND` from config:

```python
def get_product_repo() -> ProductRepository:
    from backend.config import DATABASE_BACKEND
    if DATABASE_BACKEND == "postgres":
        from backend.repositories.postgres import PostgresProductRepository
        return PostgresProductRepository()
    from backend.repositories.memory import MemoryProductRepository
    return MemoryProductRepository()
```

#### `backend/repositories/memory.py` -- In-Memory Adapters

Dict-based storage for development and testing. No persistence -- data resets on restart. Supports full CRUD, filtering, and state machine transitions with validation.

#### `backend/repositories/postgres.py` -- PostgreSQL Adapters

Production adapters using `asyncpg`. Features:
- Lazy-initialized connection pool (`min_size=2, max_size=10`)
- Dynamic INSERT from data keys (no hardcoded column lists)
- Parameterized queries (SQL injection safe)
- State machine transition validation with SELECT-then-UPDATE pattern

### `gen_routes.py` -- Route Handlers

One route module per Route contract. Each route handler:
- Accepts the repository as a FastAPI `Depends` dependency
- Accepts auth dependencies (if `infra/auth` contract exists)
- Returns HATEOAS `_links` on create
- Returns proper HTTP status codes (201 for create, 204 for delete, 404 for not found)

Supported endpoint patterns:

| Contract endpoint | Generated handler |
|-------------------|-------------------|
| `GET /` | `list_{entity}s` -- paginated list with limit/offset |
| `GET /{id}` | `get_{entity}` -- single record by ID |
| `POST /` | `create_{entity}` -- create with auto-fields (UUID, timestamps) |
| `PATCH /{id}` | `update_{entity}` -- partial update |
| `DELETE /{id}` | `delete_{entity}` -- delete, returns 204 |
| `PUT /{id}/state` | `transition_{entity}` -- state machine transition |

### `gen_app.py` -- Application Entrypoint

Generates `backend/app.py` with:
- FastAPI application with domain-specific title
- CORS middleware (configurable origins)
- Router includes for each entity
- `/health` endpoint
- `/auth/login` endpoint (if auth contract exists)

### `gen_auth.py` -- Authentication System

Only generated when the domain includes an `infra/auth` contract. Produces three files:

#### `backend/auth/interface.py`

Abstract `AuthProvider` interface:

```python
class AuthUser(BaseModel):
    id: str
    email: str
    role: str

class AuthProvider(ABC):
    async def authenticate(self, token: str) -> Optional[AuthUser]: ...
    async def create_token(self, user_data: dict) -> str: ...
    async def refresh_token(self, token: str) -> Optional[str]: ...
```

#### `backend/auth/jwt_provider.py`

JWT implementation using `python-jose` and `passlib`:
- HS256 signing algorithm
- Configurable expiration (`AUTH_TOKEN_EXPIRE_MINUTES`)
- bcrypt password hashing
- Token payload: `sub` (user ID), `email`, `role`, `exp`

#### `backend/auth/middleware.py`

FastAPI dependencies for route protection:

```python
async def require_auth(authorization: str = Header(None), ...) -> AuthUser:
    """Validates Bearer token. Returns anonymous user if AUTH_ENABLED=false."""

def require_role(*roles: str):
    """Returns a dependency that checks user role against allowed roles."""
```

When `AUTH_ENABLED=false`, `require_auth` returns an anonymous admin user -- no token needed.

---

## The `docker` Generator

The Docker generator (`forge/targets/fastapi_prod/gen_docker.py`) produces four files:

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY database/ database/
EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml

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
      AUTH_ENABLED: "${AUTH_ENABLED:-false}"
      AUTH_SECRET: "${AUTH_SECRET:-change-me}"
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
```

Key features:
- PostgreSQL schema is auto-applied via Docker entrypoint init script
- App waits for DB health check before starting
- Auth settings passed from host environment with sane defaults
- Persistent volume for database data

### .env.example

Comprehensive environment file with all variables grouped by section:
- Database (DATABASE_URL, DATABASE_BACKEND)
- Server (PORT, CORS_ORIGINS)
- Authentication (AUTH_ENABLED, AUTH_PROVIDER, AUTH_SECRET, AUTH_TOKEN_EXPIRE_MINUTES)
- AI/LLM Providers (all 6 providers)
- Healer Service (port, webhook URL)

### requirements.txt

Generated with exact minimum versions:

```
fastapi>=0.110
uvicorn>=0.29
pydantic>=2.0
asyncpg>=0.29
httpx>=0.27
```

If auth contract exists, adds:

```
python-jose[cryptography]>=3.3
passlib[bcrypt]>=1.7
python-multipart>=0.0.9
```

---

## Swapping Database Backends

The repository pattern makes backend swapping trivial:

### Memory Backend (Development)

```bash
DATABASE_BACKEND=memory
```

- No database server needed
- Data stored in Python dicts
- Resets on restart
- Useful for rapid prototyping and CI

### PostgreSQL Backend (Production)

```bash
DATABASE_BACKEND=postgres
DATABASE_URL=postgresql://specora:specora@localhost:5432/specora
```

- asyncpg connection pool (2-10 connections)
- Full SQL with proper indexes
- Persistent storage
- Apply schema first: `psql -f database/schema.sql`

### Custom Backends

To add a new backend (e.g., SQLite, MongoDB):

1. Create `backend/repositories/{backend}.py`
2. Implement the abstract interface from `base.py`
3. Add the backend name to the factory functions in `base.py`

---

## Environment Variables Reference

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://specora:specora@localhost:5432/specora` | PostgreSQL connection string |
| `DATABASE_BACKEND` | `postgres` | Backend selection: `postgres` or `memory` |

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Port the FastAPI app listens on |
| `CORS_ORIGINS` | `*` | Comma-separated CORS origins |

### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_ENABLED` | `false` (or `true` if auth contract exists) | Enable/disable auth middleware |
| `AUTH_PROVIDER` | `jwt` | Auth provider: `jwt` or `external` |
| `AUTH_SECRET` | `change-me-in-production` | JWT signing secret |
| `AUTH_TOKEN_EXPIRE_MINUTES` | `60` | JWT token expiration in minutes |

### AI / LLM Providers

| Variable | Default | Description |
|----------|---------|-------------|
| `SPECORA_AI_MODEL` | (empty) | Override model: `claude-sonnet-4-6`, `gpt-4o`, etc. |
| `ANTHROPIC_API_KEY` | (empty) | Anthropic API key |
| `OPENAI_API_KEY` | (empty) | OpenAI API key |
| `XAI_API_KEY` | (empty) | xAI (Grok) API key |
| `ZAI_API_KEY` | (empty) | Z.AI (GLM) API key |
| `GOOGLE_API_KEY` | (empty) | Google Gemini API key |
| `OLLAMA_BASE_URL` | (empty) | Ollama server URL |
| `LMSTUDIO_BASE_URL` | (empty) | LM Studio server URL |

### Healer Service

| Variable | Default | Description |
|----------|---------|-------------|
| `SPECORA_HEALER_PORT` | `8083` | Port for the Healer HTTP API |
| `SPECORA_HEALER_WEBHOOK_URL` | (empty) | Webhook URL for healer notifications |

### Editor

| Variable | Default | Description |
|----------|---------|-------------|
| `EDITOR` | (empty) | Editor for contract review |
| `VISUAL` | (empty) | Visual editor fallback |

---

## Auth System Deep Dive

### How Auth Generation Works

1. You create an `infra/auth` contract in your domain:

```yaml
apiVersion: specora.dev/v1
kind: Infra
metadata:
  name: auth
  domain: inventory
spec:
  category: auth
  config:
    provider: jwt
    roles:
      - admin
      - editor
      - viewer
  env_vars:
    AUTH_SECRET: "JWT signing secret"
    AUTH_TOKEN_EXPIRE_MINUTES: "Token expiration"
```

2. The `fastapi-prod` generator detects `infra.category == "auth"` and generates the auth system.

3. Route handlers automatically include `require_auth` and `require_role` dependencies.

### Using Auth in the Generated API

Login:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"id": "user-1", "email": "admin@example.com", "role": "admin"}'
```

Response:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

Authenticated request:

```bash
curl http://localhost:8000/products \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

### Disabling Auth

Set `AUTH_ENABLED=false` in your environment. All routes will accept requests without tokens, and `require_auth` returns an anonymous admin user.

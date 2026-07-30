"""Generate Dockerfile, docker-compose.yml, .env.example, requirements files.

Dependency policy for the generated app image:

  - **Only what `backend/` imports at runtime.** The runtime image previously
    installed pytest, and the healer image's CLI/LLM stack was audited as part
    of the same set. A deployed application should not carry a test framework.
    Test-only dependencies go to `requirements-dev.txt`, which the Dockerfile
    does not copy.
  - **Every requirement is bounded on both sides.** An unbounded `fastapi>=…`
    is how a future major release turns a deprecation (`@app.on_event`) into a
    boot failure in an image nobody rebuilt deliberately. Lower bounds are set
    at the first release without a known advisory for that package, so
    `pip-audit` resolves clean.
"""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile


def generate_docker(ir: DomainIR) -> list[GeneratedFile]:
    has_auth = any(i.category == "auth" for i in ir.infra)
    return [
        _generate_dockerfile(ir),
        _generate_entrypoint(ir),
        _generate_healer_dockerfile(ir),
        _generate_compose(ir),
        _generate_env_example(ir, has_auth),
        _generate_requirements(ir, has_auth),
        _generate_dev_requirements(ir),
        _generate_healer_requirements(ir),
    ]


def _generate_dockerfile(ir: DomainIR) -> GeneratedFile:
    # requirements-dev.txt is deliberately not copied: the runtime image must
    # not contain the test framework.
    content = """FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY database/ database/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
"""
    return GeneratedFile(path="Dockerfile", content=content, provenance=f"domain/{ir.domain}")


def _generate_entrypoint(ir: DomainIR) -> GeneratedFile:
    # Schema and migrations are applied by the application's lifespan handler,
    # not here. There used to be two implementations: this one recorded applied
    # migrations in `_specora_migrations` while backend/app.py kept its own
    # `_migrations` ledger, so every migration was applied twice on a fresh
    # database. The app-side runner is the one that can take an advisory lock
    # and wrap each migration in a transaction, so it is the one that survives.
    content = '''#!/bin/bash
set -euo pipefail

if [ "${DATABASE_BACKEND:-postgres}" = "postgres" ]; then
    echo "[specora] Waiting for database..."
    until python -c "
import asyncio, asyncpg, os
async def check():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    await conn.close()
asyncio.run(check())
" 2>/dev/null; do
        sleep 1
    done
    echo "[specora] Database is ready."
fi

echo "[specora] Starting app (schema and migrations run at startup)..."
exec uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-8000}"
'''
    return GeneratedFile(path="entrypoint.sh", content=content, provenance=f"domain/{ir.domain}")


def _generate_healer_dockerfile(ir: DomainIR) -> GeneratedFile:
    content = """FROM python:3.12-slim
WORKDIR /app
COPY requirements.healer.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# specora-core is mounted at /specora-core via docker-compose volume
ENV PYTHONPATH=/specora-core
EXPOSE 8083
CMD ["python", "-m", "forge.cli.main", "healer", "serve", "--port", "8083", "--host", "0.0.0.0"]
"""
    return GeneratedFile(
        path="Dockerfile.healer", content=content, provenance=f"domain/{ir.domain}"
    )


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
      - pgdata:/var/lib/postgresql/data
    ports:
      # Loopback only. The app reaches the database over the compose network;
      # binding 0.0.0.0 published a database with a default password to every
      # interface on the host.
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U specora"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      DATABASE_URL: postgresql://specora:specora@db:5432/specora
      DATABASE_BACKEND: postgres
      SPECORA_HEALER_URL: http://healer:8083
    depends_on:
      db:
        condition: service_healthy

  healer:
    build:
      context: .
      dockerfile: Dockerfile.healer
    ports:
      - "8083:8083"
    env_file: .env
    volumes:
      - ./domains:/app/domains
      - ./.forge:/app/.forge
      - ./backend:/app/backend
      - ./database:/app/database
      - ./frontend:/app/frontend
      - ${{SPECORA_CORE_PATH:-./../specora-core}}:/specora-core:ro
    environment:
      SPECORA_HEALER_PORT: "8083"
    depends_on:
      - app

  # Frontend — behind a profile because npm install is slow in Docker on Windows.
  # Run locally with: cd frontend && npm install && npm run dev
  # Or include in Docker with: docker compose --profile frontend up
  frontend:
    profiles: [frontend]
    build:
      context: ./frontend
      dockerfile: Dockerfile.frontend
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: http://app:8000
    depends_on:
      - app

volumes:
  pgdata:
"""
    return GeneratedFile(
        path="docker-compose.yml", content=content, provenance=f"domain/{ir.domain}"
    )


def _generate_env_example(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    lines = [
        "# =============================================================================",
        f"# {ir.domain} — Environment Configuration",
        "# =============================================================================",
        "# Generated by Specora Forge. Copy to .env and customize.",
        "",
        "",
        "# =============================================================================",
        "# Database",
        "# =============================================================================",
        "",
        "DATABASE_URL=postgresql://specora:specora@localhost:5432/specora",
        "DATABASE_BACKEND=postgres  # postgres | memory",
        "",
        "# Pool ceiling is this container's concurrency limit for database work.",
        "DATABASE_POOL_MIN_SIZE=2",
        "DATABASE_POOL_MAX_SIZE=10",
        "",
        "# Postgres cancels a statement that runs past this, freeing the connection.",
        "DATABASE_STATEMENT_TIMEOUT_MS=15000",
        "",
        "",
        "# =============================================================================",
        "# Server",
        "# =============================================================================",
        "",
        "PORT=8000",
        "",
        "# Comma-separated browser origins, e.g. https://app.example.com",
        "# The app refuses to boot on '*' unless CORS_ALLOW_CREDENTIALS=false.",
        "# Leave empty to allow no cross-origin browser access at all.",
        "CORS_ORIGINS=",
        "CORS_ALLOW_CREDENTIALS=true",
    ]

    if has_auth:
        lines.extend([
            "",
            "",
            "# =============================================================================",
            "# Authentication",
            "# =============================================================================",
            "",
            "# Required. The app refuses to boot while this is empty or still the",
            "# placeholder, because every token it issued would be forgeable.",
            "#   openssl rand -hex 32",
            "AUTH_SECRET=",
            "",
            "AUTH_PROVIDER=jwt  # jwt | external",
            "",
            "# Bound into every token and required on every token verified.",
            f"AUTH_ISSUER=specora:{ir.domain}",
            f"AUTH_AUDIENCE=specora:{ir.domain}",
            "",
            "# Access tokens cannot be revoked, so they are short-lived; the refresh",
            "# token is the revocable half and is single-use.",
            "AUTH_TOKEN_EXPIRE_MINUTES=15",
            "AUTH_REFRESH_TOKEN_EXPIRE_DAYS=14",
            "",
            "# The refresh token is also set as an httpOnly cookie. Browsers exempt",
            "# http://localhost from the Secure attribute, so only a deployment",
            "# deliberately served over plain HTTP needs this set to false.",
            "AUTH_COOKIE_SECURE=true",
            "",
            "# Authentication is declared by this domain's contracts. Setting this to",
            "# false does not disable it — the app refuses to boot instead.",
            "AUTH_ENABLED=true",
        ])

    lines.extend([
        "",
        "",
        "# =============================================================================",
        "# AI / LLM Providers (for Healer Tier 2-3 + Factory + Chat)",
        "# =============================================================================",
        "# At least one provider needed for LLM-powered self-healing.",
        "# Priority: SPECORA_AI_MODEL > ANTHROPIC > OPENAI > XAI > ZAI > OLLAMA",
        "",
        "# Override model: claude-sonnet-4-6, glm-5.1, gpt-4o, etc.",
        "SPECORA_AI_MODEL=",
        "",
        "# Anthropic (recommended) — https://console.anthropic.com/",
        "ANTHROPIC_API_KEY=",
        "",
        "# OpenAI — https://platform.openai.com/api-keys",
        "OPENAI_API_KEY=",
        "",
        "# xAI (Grok) — https://console.x.ai/",
        "XAI_API_KEY=",
        "",
        "# Z.AI (GLM) — https://z.ai — free models: glm-4.7-flash, glm-4.5-flash",
        "ZAI_API_KEY=",
        "",
        "# Local (Ollama) — https://ollama.com/",
        "OLLAMA_BASE_URL=",
        "",
        "",
        "# =============================================================================",
        "# Healer Service (runs as sidecar in Docker stack)",
        "# =============================================================================",
        "",
        "SPECORA_HEALER_PORT=8083",
        "SPECORA_HEALER_URL=http://localhost:8083  # App reports errors here",
        "SPECORA_HEALER_WEBHOOK_URL=     # Optional: POST notifications on state changes",
        "",
        "# Path to specora-core installation (for Healer Docker container)",
        "SPECORA_CORE_PATH=./../specora-core",
        "",
    ])
    return GeneratedFile(
        path=".env.example", content="\n".join(lines), provenance=f"domain/{ir.domain}"
    )


def _generate_requirements(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    # pydantic[email] pulls email-validator, which EmailStr imports at class
    # definition time — a model with an email field fails to import without it.
    needs_email = any(f.type == "email" for e in ir.entities for f in e.fields)
    pydantic = "pydantic[email]>=2.7,<3.0" if needs_email else "pydantic>=2.7,<3.0"

    deps = [
        "# Runtime dependencies of backend/. Nothing else belongs in the app image.",
        "fastapi>=0.115.3,<1.0",
        "uvicorn>=0.30,<1.0",
        pydantic,
        "asyncpg>=0.29,<0.31",
        "httpx>=0.27,<1.0",
    ]
    if has_auth:
        deps.extend([
            "",
            "# JWT: pyjwt rather than python-jose, which is unmaintained and carries",
            "# algorithm-confusion advisories. >=2.10 for the strict issuer check.",
            "pyjwt[crypto]>=2.10,<3.0",
            "# Password hashing: argon2-cffi directly rather than passlib, which is",
            "# unmaintained and whose 1.7.4 breaks against bcrypt 5.x.",
            "argon2-cffi>=23.1,<26.0",
        ])
    return GeneratedFile(
        path="requirements.txt",
        content="\n".join(deps) + "\n",
        provenance=f"domain/{ir.domain}",
    )


def _generate_dev_requirements(ir: DomainIR) -> GeneratedFile:
    # Deliberately a flat list rather than `-r requirements.txt`: every
    # generated requirements file is fed to pip-audit as a plain requirement
    # set, and an include directive there resolves against the wrong directory.
    deps = [
        "# Test-only. The runtime Dockerfile does not copy or install this file.",
        "# Install alongside the runtime set:",
        "#   pip install -r requirements.txt -r requirements-dev.txt",
        # 9.0.3 is the first release without PYSEC-2026-1845.
        "pytest>=9.0.3,<10.0",
    ]
    return GeneratedFile(
        path="requirements-dev.txt",
        content="\n".join(deps) + "\n",
        provenance=f"domain/{ir.domain}",
    )


def _generate_healer_requirements(ir: DomainIR) -> GeneratedFile:
    # Installed into Dockerfile.healer only. The healer runs specora-core's own
    # CLI, so it needs the CLI and LLM stack that the app image must not have.
    deps = [
        "fastapi>=0.115.3,<1.0",
        "uvicorn>=0.30,<1.0",
        "pydantic>=2.7,<3.0",
        "httpx>=0.27,<1.0",
        "pyyaml>=6.0.1,<7.0",
        "jsonschema>=4.23,<5.0",
        "click>=8.1.7,<9.0",
        "rich>=13.7,<15.0",
        "deepdiff>=7.0,<9.0",
        "python-dotenv>=1.0,<2.0",
        "prompt_toolkit>=3.0.47,<4.0",
        "# LLM providers for Tier 2-3 healing",
        "openai>=1.55,<3.0",
        "anthropic>=0.40,<1.0",
    ]
    return GeneratedFile(
        path="requirements.healer.txt",
        content="\n".join(deps) + "\n",
        provenance=f"domain/{ir.domain}",
    )

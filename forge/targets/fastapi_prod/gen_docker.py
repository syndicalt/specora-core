"""Generate Dockerfile, docker-compose.yml, .env.example, requirements.txt."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile, provenance_header


def generate_docker(ir: DomainIR) -> list[GeneratedFile]:
    has_auth = any(i.category == "auth" for i in ir.infra)
    return [
        _generate_dockerfile(ir),
        _generate_entrypoint(ir),
        _generate_healer_dockerfile(ir),
        _generate_compose(ir),
        _generate_env_example(ir, has_auth),
        _generate_requirements(ir, has_auth),
        _generate_healer_requirements(ir),
    ]


def _generate_dockerfile(ir: DomainIR) -> GeneratedFile:
    content = f"""FROM python:3.12-slim
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
    content = '''#!/bin/bash
set -e

# Wait for database to be ready
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

# Apply baseline schema if tables don't exist
TABLE_COUNT=$(python -c "
import asyncio, asyncpg, os
async def count():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    result = await conn.fetchval(
        \\"SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'\\"
    )
    await conn.close()
    print(result)
asyncio.run(count())
")

if [ "$TABLE_COUNT" = "0" ]; then
    echo "[specora] Fresh database — applying baseline schema..."
    python -c "
import asyncio, asyncpg, os
from pathlib import Path
async def apply():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    schema = Path('database/schema.sql').read_text()
    await conn.execute(schema)
    # Create migrations tracking table
    await conn.execute(\\"CREATE TABLE IF NOT EXISTS _specora_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())\\")
    # Mark all existing migrations as applied (they're included in the baseline)
    migrations_dir = Path('database/migrations')
    if migrations_dir.exists():
        for f in sorted(migrations_dir.glob('*.sql')):
            await conn.execute(\\"INSERT INTO _specora_migrations (filename) VALUES (\\\\$1) ON CONFLICT DO NOTHING\\", f.name)
    await conn.close()
asyncio.run(apply())
"
    echo "[specora] Baseline schema applied."
else
    echo "[specora] Existing database — checking for pending migrations..."
    # Create tracking table if it doesn't exist (upgrade from pre-migration installs)
    python -c "
import asyncio, asyncpg, os
async def ensure():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    await conn.execute(\\"CREATE TABLE IF NOT EXISTS _specora_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())\\")
    await conn.close()
asyncio.run(ensure())
"
fi

# Apply pending migrations
python -c "
import asyncio, asyncpg, os
from pathlib import Path
async def migrate():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    migrations_dir = Path('database/migrations')
    if not migrations_dir.exists():
        await conn.close()
        return
    applied = set(row['filename'] for row in await conn.fetch('SELECT filename FROM _specora_migrations'))
    pending = sorted(f for f in migrations_dir.glob('*.sql') if f.name not in applied)
    for migration in pending:
        print(f'[specora] Applying migration: {migration.name}')
        sql = migration.read_text()
        await conn.execute(sql)
        await conn.execute('INSERT INTO _specora_migrations (filename) VALUES (\\$1)', migration.name)
    if not pending:
        print('[specora] No pending migrations.')
    else:
        print(f'[specora] Applied {len(pending)} migration(s).')
    await conn.close()
asyncio.run(migrate())
"

# Start the app
echo "[specora] Starting app..."
exec uvicorn backend.app:app --host 0.0.0.0 --port 8000
'''
    return GeneratedFile(path="entrypoint.sh", content=content, provenance=f"domain/{ir.domain}")


def _generate_healer_dockerfile(ir: DomainIR) -> GeneratedFile:
    content = f"""FROM python:3.12-slim
WORKDIR /app
COPY requirements.healer.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# specora-core is mounted at /specora-core via docker-compose volume
ENV PYTHONPATH=/specora-core
EXPOSE 8083
CMD ["python", "-m", "forge.cli.main", "healer", "serve", "--port", "8083", "--host", "0.0.0.0"]
"""
    return GeneratedFile(path="Dockerfile.healer", content=content, provenance=f"domain/{ir.domain}")


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
    return GeneratedFile(path="docker-compose.yml", content=content, provenance=f"domain/{ir.domain}")


def _generate_env_example(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    lines = [
        f"# =============================================================================",
        f"# {ir.domain} — Environment Configuration",
        f"# =============================================================================",
        f"# Generated by Specora Forge. Copy to .env and customize.",
        "",
        "",
        "# =============================================================================",
        "# Database",
        "# =============================================================================",
        "",
        "DATABASE_URL=postgresql://specora:specora@localhost:5432/specora",
        "DATABASE_BACKEND=postgres  # postgres | memory",
        "",
        "",
        "# =============================================================================",
        "# Server",
        "# =============================================================================",
        "",
        "PORT=8000",
        "CORS_ORIGINS=*",
    ]

    if has_auth:
        lines.extend([
            "",
            "",
            "# =============================================================================",
            "# Authentication",
            "# =============================================================================",
            "",
            "AUTH_ENABLED=true",
            "AUTH_PROVIDER=jwt  # jwt | external",
            "AUTH_SECRET=change-me-in-production",
            "AUTH_TOKEN_EXPIRE_MINUTES=60",
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
        "SPECORA_AI_MODEL=               # Override model: claude-sonnet-4-6, glm-5.1, gpt-4o, etc.",
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
    return GeneratedFile(path=".env.example", content="\n".join(lines), provenance=f"domain/{ir.domain}")


def _generate_requirements(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    deps = [
        "fastapi>=0.110",
        "uvicorn>=0.29",
        "pydantic>=2.0",
        "asyncpg>=0.29",
        "httpx>=0.27",
        "pytest>=8.0",
    ]
    if has_auth:
        deps.extend([
            "python-jose[cryptography]>=3.3",
            "passlib[bcrypt]>=1.7",
            "python-multipart>=0.0.9",
        ])
    return GeneratedFile(path="requirements.txt", content="\n".join(deps) + "\n", provenance=f"domain/{ir.domain}")


def _generate_healer_requirements(ir: DomainIR) -> GeneratedFile:
    deps = [
        "fastapi>=0.110",
        "uvicorn>=0.29",
        "pydantic>=2.0",
        "httpx>=0.27",
        "pyyaml>=6.0",
        "jsonschema>=4.20",
        "click>=8.1",
        "rich>=13.0",
        "deepdiff>=7.0",
        "python-dotenv>=1.0",
        "prompt_toolkit>=3.0",
        "# LLM providers for Tier 2-3 healing",
        "openai>=1.0",
        "anthropic>=0.25",
    ]
    return GeneratedFile(path="requirements.healer.txt", content="\n".join(deps) + "\n", provenance=f"domain/{ir.domain}")

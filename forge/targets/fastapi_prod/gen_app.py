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
        "import os",
        "import traceback",
        "",
        "from fastapi import FastAPI, Request",
        "from fastapi.middleware.cors import CORSMiddleware",
        "from fastapi.responses import JSONResponse",
        "",
        "from backend.config import CORS_ORIGINS, PORT, DATABASE_URL, DATABASE_BACKEND",
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
        "",
        "# ── Migration runner ─────────────────────────────────────────────",
        "",
        "import glob",
        "",
        "@app.on_event('startup')",
        "async def run_migrations():",
        '    """Apply pending database migrations on startup."""',
        "    if DATABASE_BACKEND != 'postgres':",
        "        return",
        "    try:",
        "        import asyncpg",
        "        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)",
        "        async with pool.acquire() as conn:",
        "            # Create migrations table",
        '            await conn.execute("""',
        "                CREATE TABLE IF NOT EXISTS _migrations (",
        "                    name TEXT PRIMARY KEY,",
        "                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "                )",
        '            """)',
        "            # Get applied migrations",
        '            rows = await conn.fetch("SELECT name FROM _migrations")',
        "            applied = {r['name'] for r in rows}",
        "            # Find and apply pending",
        "            migration_files = sorted(glob.glob('database/migrations/*.sql'))",
        "            for mf in migration_files:",
        "                name = mf.split('/')[-1].split('\\\\')[-1]",
        "                if name not in applied:",
        "                    sql = open(mf).read()",
        "                    await conn.execute(sql)",
        '                    await conn.execute("INSERT INTO _migrations (name) VALUES ($1)", name)',
        f"                    print(f'Applied migration: {{name}}')",
        "        await pool.close()",
        "    except Exception as e:",
        f"        print(f'Migration error: {{e}}')",
        "        raise SystemExit(1)",
        "",
        "",
        "# ── Error reporting to Healer ──────────────────────────────────────",
        "",
        'HEALER_URL = os.getenv("SPECORA_HEALER_URL", "")',
        "",
        "",
    ]

    # Build route-to-FQN mapping for healer error reporting
    lines.append("# Map route prefixes to contract FQNs for healer error reporting")
    lines.append("ROUTE_TO_FQN = {")
    for route in ir.routes:
        entity_name = route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name
        base = route.base_path or f"/{entity_name}s"
        lines.append(f'    "{base}": "{route.entity_fqn}",')
    lines.append("}")

    lines.extend([
        "",
        "",
        "def _infer_contract_fqn(path: str) -> str:",
        '    for prefix, fqn in ROUTE_TO_FQN.items():',
        "        if path.startswith(prefix):",
        "            return fqn",
        '    return ""',
        "",
        "",
        "@app.exception_handler(Exception)",
        "async def healer_error_reporter(request: Request, exc: Exception):",
        "    tb = traceback.format_exc()",
        "    fqn = _infer_contract_fqn(str(request.url.path))",
        "    if HEALER_URL:",
        "        try:",
        "            import httpx",
        "            async with httpx.AsyncClient() as client:",
        "                payload = {",
        '                    "source": "runtime",',
        '                    "contract_fqn": fqn,',
        '                    "error": str(exc),',
        '                    "stacktrace": tb,',
        '                    "context": {',
        '                        "request_path": str(request.url.path),',
        '                        "method": request.method,',
        '                        "status_code": 500,',
        "                    },",
        "                }",
        '                await client.post(f"{HEALER_URL}/healer/ingest", json=payload, timeout=5.0)',
        "        except Exception:",
        "            pass",
        "    return JSONResponse(",
        "        status_code=500,",
        '        content={"error": "internal_server_error", "detail": str(exc)},',
        "    )",
    ])

    lines.extend([
        "",
        "",
        *route_includes,
        "",
        "",
        '@app.get("/health")',
        "async def health():",
        f'    return {{"status": "ok", "domain": "{ir.domain}", "healer": HEALER_URL or "not configured"}}',
        "",
    ])

    if has_auth:
        lines.insert(lines.index("from backend.config import CORS_ORIGINS, PORT, DATABASE_URL, DATABASE_BACKEND") + 1,
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

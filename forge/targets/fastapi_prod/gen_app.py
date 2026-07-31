"""Generate the FastAPI application entrypoint.

# Login endpoint — required contract shape

A generated app emits `POST /auth/login` **only** when the auth contract names
the entity that stores credentials:

    apiVersion: specora.dev/v1
    kind: Infra
    metadata: {name: auth, domain: shop}
    spec:
      category: auth
      config:
        provider: jwt
        roles: [admin, member]
        user_entity: entity/shop/account   # required to emit /auth/login
        identity_field: email              # default "email"
        password_field: password_hash      # default "password_hash"
        role_field: role                   # default "role"
        active_field: is_active            # optional; falsy value denies login

Without `user_entity` there is no stored credential to check, so no login
endpoint is emitted and the deployment is expected to obtain its tokens from its
own identity provider. This replaces a handler that called `create_token(body)`
on an unauthenticated request body: `POST /auth/login {"id": "x", "role":
"admin"}` returned a valid admin JWT to anyone who asked.

Two cases fail at generation time rather than at deploy time (CODEGEN_CONTRACT
§5):

  - `user_entity` is declared but the entity, or one of the named fields, does
    not exist. A login handler that cannot read a password hash is worse than
    no login handler.
  - The build has role-protected endpoints and `provider: jwt`, but no
    `user_entity`. This app is then the only possible issuer of the tokens its
    own routes demand, and it has no way to issue one — every route 401s
    forever. A different `provider` is exempt: an external IdP mints the tokens
    and there is nothing for this app to verify a credential against.

`/auth/refresh` is emitted whenever auth is declared, and rotates the token pair
(see `gen_auth`, `backend/auth/token_store.py`). Both endpoints also deposit the
refresh token in an httpOnly, Secure, SameSite=Lax cookie scoped to
`/auth/refresh`, and `/auth/refresh` accepts it from there when the body omits
it, so a browser client never has to keep it anywhere script-readable.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

from forge.ir.model import DomainIR, EntityIR, InfraIR, RouteIR
from forge.targets.base import GeneratedFile, GenerationError, provenance_header
from forge.targets.fastapi_prod.gen_auth import auth_contract, protected_routes
from forge.targets.naming import class_name, module_slug, py_identifier, repo_accessor

_DEFAULT_IDENTITY_FIELD = "email"
_DEFAULT_PASSWORD_FIELD = "password_hash"
_DEFAULT_ROLE_FIELD = "role"


@dataclass(frozen=True)
class _LoginSpec:
    """Everything the login handler needs, resolved and checked at build time."""

    repository_class: str
    repository_accessor: str
    identity_field: str
    identity_type: str
    password_field: str
    role_field: str
    active_field: str | None


def generate_app(ir: DomainIR) -> GeneratedFile:
    auth_infra = auth_contract(ir)
    login = _resolve_login(ir, auth_infra) if auth_infra is not None else None
    guarded = bool(auth_infra is not None and protected_routes(ir, auth_infra))

    sections = [
        _imports_section(ir, auth_infra is not None, guarded, login),
        _migrations_section(ir),
        _lifespan_section(auth_infra is not None),
        _app_section(ir, guarded),
        _error_handling_section(ir),
        _routers_section(ir),
    ]
    if auth_infra is not None:
        sections.append(_auth_endpoints_section(login))
    sections.append(_health_section(ir))

    return GeneratedFile(
        path="backend/app.py",
        content="".join(sections),
        provenance=f"domain/{ir.domain}",
    )


# =============================================================================
# Sections — emitted in dependency order, so the module executes top to bottom
# =============================================================================


def _imports_section(
    ir: DomainIR, has_auth: bool, guarded: bool, login: _LoginSpec | None
) -> str:
    fastapi_names = ["FastAPI", "Request"]
    if has_auth:
        fastapi_names.extend(["HTTPException", "Response"])
    if login is not None:
        fastapi_names.append("Depends")

    third_party = [
        f"from fastapi import {', '.join(sorted(fastapi_names))}",
        "from fastapi.middleware.cors import CORSMiddleware",
        "from fastapi.responses import JSONResponse",
        "from starlette.background import BackgroundTask",
    ]
    if has_auth:
        pydantic_names = ["BaseModel", "Field"]
        if login is not None and login.identity_type == "EmailStr":
            pydantic_names.append("EmailStr")
        third_party.append(f"from pydantic import {', '.join(sorted(pydantic_names))}")

    # Sorted, so the generated module satisfies an isort-style import check the
    # same way a hand-written one would.
    config_names = [
        "CORS_ALLOW_CREDENTIALS",
        "CORS_ORIGINS",
        "DATABASE_BACKEND",
        "DATABASE_URL",
        "HEALER_INGEST_TOKEN",
        "HEALER_URL",
    ]
    if has_auth:
        config_names.extend(["AUTH_COOKIE_SECURE", "AUTH_REFRESH_TOKEN_EXPIRE_DAYS"])
    local = [
        "from backend.config import (",
        *(f"    {name}," for name in sorted(config_names)),
        ")",
    ]
    local_single: list[str] = []
    if has_auth:
        interface_names = ["TokenPair"] + (["AuthUser"] if login is not None else [])
        middleware_names = ["get_auth_provider"] + (
            ["enforce_protected_routes"] if guarded else []
        )
        local_single.append(
            f"from backend.auth.interface import {', '.join(sorted(interface_names))}"
        )
        local_single.append(
            f"from backend.auth.middleware import {', '.join(sorted(middleware_names))}"
        )
        local_single.append("from backend.auth.token_store import get_refresh_token_store")
    if login is not None:
        local_single.append("from backend.auth.jwt_provider import verify_password")
        local_single.append(
            f"from backend.repositories.base import "
            f"{login.repository_class}Repository, {login.repository_accessor}"
        )
    for route in ir.routes:
        slug = module_slug(_entity_stem(route), route.domain, multi_domain=ir.multi_domain)
        local_single.append(
            f"from backend.routes_{slug} import router as {_router_var(route, ir)}"
        )

    return "\n".join([
        provenance_header("python", f"domain/{ir.domain}", "FastAPI application entrypoint"),
        "from __future__ import annotations",
        "",
        "import logging",
        "import traceback",
        "import uuid",
        "from contextlib import asynccontextmanager",
        "from pathlib import Path",
        "",
        *sorted(third_party),
        "",
        *sorted(local_single + ["\n".join(local)]),
        "",
        f'logger = logging.getLogger("{ir.domain}")',
        "",
    ])


def _migrations_section(ir: DomainIR) -> str:
    # A per-domain lock id, so two Specora applications sharing one database
    # cluster do not serialise against each other. crc32 is stable across
    # processes and interpreter versions, unlike hash().
    lock_id = zlib.crc32(ir.domain.encode("utf-8"))
    return f'''

# ── Schema and migrations ────────────────────────────────────────────────────

MIGRATION_LOCK_ID = {lock_id}
_MIGRATIONS_DIR = Path("database/migrations")
_SCHEMA_FILE = Path("database/schema.sql")

_LEDGER_DDL = """
    CREATE TABLE IF NOT EXISTS _specora_migrations (
        filename    TEXT PRIMARY KEY,
        applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

# Internal bookkeeping tables are underscore-prefixed. Ignoring them is what
# distinguishes "empty database, apply the baseline schema" from "existing
# database, apply only what is pending".
_COUNT_ENTITY_TABLES = """
    SELECT count(*) FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_type = 'BASE TABLE'
      AND left(table_name, 1) <> '_'
"""


async def _apply_schema_and_migrations() -> None:
    """Bring the database up to the generated schema.

    This runs in every worker of every replica, so all of it sits under one
    advisory lock: without it, N uvicorn workers booting together race to apply
    the same DDL and all but one fail. Each migration commits together with its
    ledger row, so a crash between the two cannot leave a migration applied but
    unrecorded — and therefore applied a second time on the next boot.
    """
    import asyncpg

    # A dedicated startup pool with no statement timeout: an index build or a
    # table rewrite legitimately runs far longer than a request-path query, and
    # having Postgres cancel one halfway is worse than waiting for it.
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
            try:
                await conn.execute(_LEDGER_DDL)
                pending = sorted(_MIGRATIONS_DIR.glob("*.sql")) if _MIGRATIONS_DIR.is_dir() else []

                if await conn.fetchval(_COUNT_ENTITY_TABLES) == 0 and _SCHEMA_FILE.is_file():
                    # The baseline schema already contains every migration, so
                    # record them instead of replaying them on top of it.
                    async with conn.transaction():
                        await conn.execute(_SCHEMA_FILE.read_text(encoding="utf-8"))
                        for migration in pending:
                            await conn.execute(
                                "INSERT INTO _specora_migrations (filename) VALUES ($1) "
                                "ON CONFLICT DO NOTHING",
                                migration.name,
                            )
                    logger.info(
                        "Applied baseline schema (%d migration(s) recorded).", len(pending)
                    )
                    return

                applied = {{
                    row["filename"]
                    for row in await conn.fetch("SELECT filename FROM _specora_migrations")
                }}
                for migration in pending:
                    if migration.name in applied:
                        continue
                    async with conn.transaction():
                        await conn.execute(migration.read_text(encoding="utf-8"))
                        await conn.execute(
                            "INSERT INTO _specora_migrations (filename) VALUES ($1)",
                            migration.name,
                        )
                    logger.info("Applied migration %s", migration.name)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)
    finally:
        await pool.close()
'''


def _lifespan_section(has_auth: bool) -> str:
    lines = [
        "",
        "",
        "@asynccontextmanager",
        "async def lifespan(app: FastAPI):",
        '    """Startup and shutdown.',
        "",
        "    Anything raised here aborts the boot by design: a container that cannot",
        "    migrate its database, or cannot prepare its token store, must fail the",
        "    deploy rather than start serving broken requests.",
        '    """',
        '    if DATABASE_BACKEND == "postgres":',
        "        await _apply_schema_and_migrations()",
    ]
    if has_auth:
        lines.append("    await get_refresh_token_store().initialize()")
    lines.extend([
        "",
        "    # A healer that is configured but silently receiving nothing is worse",
        "    # than one that is obviously not configured, and the ingest endpoint",
        "    # rejects an unauthenticated report.",
        "    if HEALER_URL and not HEALER_INGEST_TOKEN:",
        "        logger.warning(",
        '            "SPECORA_HEALER_URL is set to %s but SPECORA_HEALER_INGEST_TOKEN "',
        '            "is empty; the healer will reject every error report and none of "',
        '            "them will be recorded.",',
        "            HEALER_URL,",
        "        )",
        "",
        "    yield",
        "",
    ])
    return "\n".join(lines)


def _app_section(ir: DomainIR, guarded: bool) -> str:
    lines = [
        "",
        f'app = FastAPI(title="Specora Generated API — {ir.domain}", lifespan=lifespan)',
        "",
    ]
    if guarded:
        lines.extend([
            "# infra/auth protected_routes, enforced ahead of every handler. Added",
            "# before CORS so that CORS ends up the outer layer and its headers are",
            "# present on the 401/403 this can return.",
            'app.middleware("http")(enforce_protected_routes)',
            "",
        ])
    lines.extend([
        "# No CORS middleware at all until the deployment names its origins, so a",
        "# default container emits no CORS headers to be misread. backend.config",
        "# additionally refuses to boot on '*' together with credentials: an echoed",
        "# Origin plus allow-credentials lets any site call this API with the",
        "# browser's cookies attached.",
        "if CORS_ORIGINS:",
        "    app.add_middleware(",
        "        CORSMiddleware,",
        "        allow_origins=CORS_ORIGINS,",
        "        allow_credentials=CORS_ALLOW_CREDENTIALS,",
        '        allow_methods=["*"],',
        '        allow_headers=["*"],',
        "    )",
        "",
    ])
    return "\n".join(lines)


def _error_handling_section(ir: DomainIR) -> str:
    entries = "\n".join(
        f'    "{route.base_path or "/" + _entity_stem(route) + "s"}": "{route.entity_fqn}",'
        for route in ir.routes
    )
    return f'''

# ── Error handling ───────────────────────────────────────────────────────────

# Route prefix -> contract FQN, so a healer report names the contract to fix.
ROUTE_TO_FQN = {{
{entries}
}}


def _infer_contract_fqn(path: str) -> str:
    for prefix, fqn in ROUTE_TO_FQN.items():
        if path.startswith(prefix):
            return fqn
    return ""


async def _report_to_healer(payload: dict) -> None:
    """Post a failure to the healer after the response has already been sent.

    Reporting must never sit on the request path. Awaiting a 5s POST before
    responding turns an error storm into an outage, and the healer being
    unreachable is exactly when errors are most likely.

    Both failure modes are logged. httpx does not raise on a 4xx, so an ingest
    endpoint rejecting the bearer token would otherwise be indistinguishable
    from a delivered report — silence on the one path whose job is to make
    failures visible.
    """
    import httpx

    correlation_id = payload["context"]["correlation_id"]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{{HEALER_URL}}/healer/ingest",
                json=payload,
                headers={{"Authorization": f"Bearer {{HEALER_INGEST_TOKEN}}"}},
            )
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Healer report %s was not delivered: %s", correlation_id, exc)
        return

    if response.status_code >= 400:
        # Truncated: the body is another service's output and belongs in the
        # log bounded, not verbatim.
        logger.warning(
            "Healer rejected report %s with %d: %s",
            correlation_id,
            response.status_code,
            response.text[:200],
        )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    correlation_id = uuid.uuid4().hex
    stacktrace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error(
        "Unhandled error %s on %s %s\\n%s",
        correlation_id,
        request.method,
        request.url.path,
        stacktrace,
    )

    background = None
    if HEALER_URL:
        background = BackgroundTask(
            _report_to_healer,
            {{
                "source": "runtime",
                "contract_fqn": _infer_contract_fqn(request.url.path),
                "error": str(exc),
                "stacktrace": stacktrace,
                "context": {{
                    "request_path": request.url.path,
                    "method": request.method,
                    "status_code": 500,
                    "correlation_id": correlation_id,
                }},
            }},
        )

    # The exception text stays server-side: asyncpg quotes the failing statement
    # along with table and column names, and a stack trace names internals. The
    # correlation id is the only thing a caller needs in order for support to
    # find the log line.
    return JSONResponse(
        status_code=500,
        content={{"error": "internal_server_error", "correlation_id": correlation_id}},
        background=background,
    )
'''


def _routers_section(ir: DomainIR) -> str:
    if not ir.routes:
        return "\n"
    includes = [f"app.include_router({_router_var(route, ir)})" for route in ir.routes]
    return "\n" + "\n".join(includes) + "\n"


def _auth_endpoints_section(login: _LoginSpec | None) -> str:
    section = '''

# ── Authentication ───────────────────────────────────────────────────────────

# The refresh token is also deposited in a cookie so a browser client has an
# XSS-proof place to keep it: httpOnly puts it out of reach of script, the path
# keeps it off every request that is not an auth call, and SameSite stops
# another site from posting it. The JSON body still carries it for non-browser
# clients.
#
# The path is /auth rather than /auth/refresh because a cookie is only sent to
# paths under its own: scoped to /auth/refresh, the browser would not send it to
# /auth/logout, and logout could not revoke the session it is being asked to end.
REFRESH_COOKIE_NAME = "specora_refresh_token"
REFRESH_COOKIE_PATH = "/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=AUTH_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    # Every attribute must match the cookie that was set, or the browser treats
    # this as a different cookie and the original survives the deletion.
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
    )


def _unauthenticated(error: str) -> JSONResponse:
    """A 401 that also clears the refresh cookie.

    Leaving a cookie the server has already revoked means the browser retries a
    dead token on every subsequent call instead of prompting for a login.
    """
    response = JSONResponse(
        status_code=401,
        content={"error": error},
        headers={"WWW-Authenticate": "Bearer"},
    )
    _clear_refresh_cookie(response)
    return response


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=1)


@app.post("/auth/refresh")
async def refresh(request: Request, response: Response, body: RefreshRequest) -> TokenPair:
    """Exchange a refresh token for a new pair. The presented token is spent."""
    presented = body.refresh_token or request.cookies.get(REFRESH_COOKIE_NAME)
    if not presented:
        return _unauthenticated("missing_refresh_token")

    tokens = await get_auth_provider().rotate_refresh(presented)
    if tokens is None:
        return _unauthenticated("invalid_refresh_token")

    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


class LogoutResponse(BaseModel):
    status: str = "signed_out"


@app.post("/auth/logout")
async def logout(
    request: Request, response: Response, body: RefreshRequest
) -> LogoutResponse:
    """Sign out of all devices: revoke every refresh token for this user.

    **This ends every session for the account, not just the calling client.**
    Any other browser or device signed in as this user must log in again.

    That is deliberate. If a refresh token was stolen and the thief has already
    rotated it once, the copy the user holds is the dead one and the thief's is
    live — revoking only the token presented would leave running precisely the
    session the user is trying to end.

    Clearing the cookie alone would only hide the credential from one browser; a
    copy captured beforehand would still be redeemable at `/auth/refresh`. The
    token is therefore revoked server-side before the cookie is cleared.

    Accepts the refresh token from the JSON body or from the `specora_refresh_token`
    cookie. Always succeeds — with no token, an expired or malformed one, or on a
    repeat call. Logout is the one operation a client must always be able to
    complete; returning an error for an already-dead session leaves callers
    retrying or, worse, treating the session as still live.
    """
    presented = body.refresh_token or request.cookies.get(REFRESH_COOKIE_NAME)
    if presented:
        await get_auth_provider().revoke_refresh(presented)
    _clear_refresh_cookie(response)
    return LogoutResponse()
'''
    if login is None:
        return section
    return section + _login_block(login)


def _login_block(login: _LoginSpec) -> str:
    # A deactivated account is checked after the hash comparison, not instead of
    # it, so "deactivated" and "wrong password" stay indistinguishable to a
    # caller and cost the same.
    active_check = (
        f' or not record.get("{login.active_field}")' if login.active_field else ""
    )
    return f'''

class LoginRequest(BaseModel):
    {login.identity_field}: {login.identity_type}
    password: str = Field(min_length=1, max_length=1024)


@app.post("/auth/login")
async def login(
    body: LoginRequest,
    response: Response,
    repo: {login.repository_class}Repository = Depends({login.repository_accessor}),
) -> TokenPair:
    """Verify a stored credential and issue a token pair."""
    # limit=2 rather than 1: if the identity field turns out not to be unique
    # the request is ambiguous and must fail, not pick an arbitrary account.
    page = await repo.list(
        limit=2, filters={{"{login.identity_field}": body.{login.identity_field}}}
    )
    record = page.items[0] if len(page.items) == 1 else None

    # verify_password runs against a decoy hash when there is no such account,
    # so an unknown account and a wrong password cost the same and response
    # timing does not enumerate users.
    verified = verify_password(body.password, (record or {{}}).get("{login.password_field}"))
    if not verified or record is None{active_check}:
        raise HTTPException(
            401,
            detail={{"error": "invalid_credentials"}},
            headers={{"WWW-Authenticate": "Bearer"}},
        )

    tokens = await get_auth_provider().issue_tokens(
        AuthUser(
            id=str(record["id"]),
            email=str(record.get("{login.identity_field}", "")),
            role=str(record.get("{login.role_field}", "")),
        )
    )
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens
'''


def _health_section(ir: DomainIR) -> str:
    return f'''

@app.get("/health")
async def health():
    return {{
        "status": "ok",
        "domain": "{ir.domain}",
        "healer": HEALER_URL or "not configured",
    }}
'''


# =============================================================================
# Contract resolution
# =============================================================================


def _resolve_login(ir: DomainIR, auth_infra: InfraIR) -> _LoginSpec | None:
    """Resolve the credential store, or None when the contract declares none."""
    user_entity_fqn = auth_infra.config.get("user_entity")
    if not user_entity_fqn:
        provider = str(auth_infra.config.get("provider", "jwt")).lower()
        if provider == "jwt" and _has_role_protection(ir, auth_infra):
            raise GenerationError(
                f"{auth_infra.fqn}: routes in this build require roles, and "
                f"provider is 'jwt', so this application is the only thing that "
                f"could issue the tokens they demand — but config.user_entity is "
                f"not set, so there is no credential to verify and no login "
                f"endpoint can be emitted. The result would deploy and 401 every "
                f"request forever. Declare config.user_entity (with "
                f"identity_field / password_field), or set provider to an "
                f"external issuer."
            )
        return None

    entity = next((e for e in ir.entities if e.fqn == user_entity_fqn), None)
    if entity is None:
        raise GenerationError(
            f"{auth_infra.fqn}: config.user_entity is {user_entity_fqn!r}, which is "
            f"not an entity in this build (have: "
            f"{sorted(e.fqn for e in ir.entities) or 'none'}). The login handler "
            f"would have no credential store to verify against."
        )

    config = auth_infra.config
    identity_field = _require_field(
        auth_infra, entity, config, "identity_field", _DEFAULT_IDENTITY_FIELD
    )
    password_field = _require_field(
        auth_infra, entity, config, "password_field", _DEFAULT_PASSWORD_FIELD
    )
    # A role claim is only meaningful if the contract declares roles to check
    # it against; require_role denies everything otherwise.
    role_field = (
        _require_field(auth_infra, entity, config, "role_field", _DEFAULT_ROLE_FIELD)
        if config.get("roles")
        else _DEFAULT_ROLE_FIELD
    )
    # Optional: no default, because inventing one would silently deny every
    # login on an entity that happens to have a falsy field by that name.
    active_field = (
        _require_field(auth_infra, entity, config, "active_field", "")
        if config.get("active_field")
        else None
    )

    return _LoginSpec(
        repository_class=class_name(entity.name, entity.domain, multi_domain=ir.multi_domain),
        repository_accessor=repo_accessor(entity.name, entity.domain, multi_domain=ir.multi_domain),
        identity_field=identity_field,
        identity_type="EmailStr" if _field_type(entity, identity_field) == "email" else "str",
        password_field=password_field,
        role_field=role_field,
        active_field=active_field,
    )


def _has_role_protection(ir: DomainIR, auth_infra: InfraIR) -> bool:
    """Whether any request in this build can only be served with a role claim."""
    return bool(auth_infra.config.get("protected_routes")) or any(
        endpoint.roles for route in ir.routes for endpoint in route.endpoints
    )


def _entity_stem(route: RouteIR) -> str:
    """The entity name a route's module and repository are keyed by."""
    return route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name


def _router_var(route: RouteIR, ir: DomainIR) -> str:
    """The local name a route module's `router` is imported under."""
    slug = module_slug(_entity_stem(route), route.domain, multi_domain=ir.multi_domain)
    return py_identifier(f"{slug}_router")


def _field_type(entity: EntityIR, name: str) -> str:
    field = next((f for f in entity.fields if f.name == name), None)
    return field.type if field else ""


def _require_field(
    auth_infra: InfraIR, entity: EntityIR, config: dict, key: str, default: str
) -> str:
    name = str(config.get(key, default))
    if not any(f.name == name for f in entity.fields):
        raise GenerationError(
            f"{auth_infra.fqn}: config.{key} is {name!r}, but {entity.fqn} has no "
            f"such field (have: {sorted(f.name for f in entity.fields)}). Add the "
            f"field to the entity contract, or point {key} at an existing one."
        )
    return name

"""Generate the auth package from an `infra/<domain>/auth` contract.

Four modules are produced:

    backend/auth/interface.py    — AuthUser, TokenPair, AuthProvider ABC
    backend/auth/jwt_provider.py — PyJWT + argon2 implementation
    backend/auth/token_store.py  — the refresh-token ledger (rotation/revocation)
    backend/auth/middleware.py   — request-time authn/authz

Contract shape consumed here (`spec.config`):

    roles: [admin, member, ...]        # the closed set of role names
    protected_routes:                  # optional, enforced app-wide
      - path: /tenants                 # must be the base_path of a route contract
        methods: [POST, PATCH, DELETE]
        roles: [admin]                 # must be a subset of `roles`

`protected_routes` used to be read only by the test generator, so a contract
could declare a route protected and the running app would not enforce it. It is
now compiled into `PROTECTED_ROUTES` and applied by a middleware ahead of every
handler; anything declared that cannot be enforced (an unknown path, an unknown
role, a bad method) fails generation instead.

The `AUTH_ENABLED=false` bypass that used to short-circuit both `require_auth`
and `require_role` is gone. It returned `role="admin"` — the most privileged
role in the contract — to unauthenticated callers. Whether authentication
exists is a property of the contracts, not of an environment variable; see
gen_config, which now refuses to boot if the override is set.
"""
from __future__ import annotations

from forge.ir.model import DomainIR, InfraIR
from forge.targets.base import GeneratedFile, GenerationError, provenance_header

_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def generate_auth(ir: DomainIR) -> list[GeneratedFile]:
    """Generate auth files if an infra/auth contract exists."""
    auth_infra = auth_contract(ir)
    if not auth_infra:
        return []

    return [
        _generate_interface(auth_infra),
        _generate_token_store(auth_infra),
        _generate_jwt_provider(auth_infra),
        _generate_middleware(ir, auth_infra),
    ]


def auth_contract(ir: DomainIR) -> InfraIR | None:
    """Return the domain's auth infra contract, if it declares one."""
    return next((i for i in ir.infra if i.category == "auth"), None)


def protected_routes(ir: DomainIR, infra: InfraIR) -> list[tuple[str, list[str], list[str]]]:
    """Resolve and validate `config.protected_routes` into (path, methods, roles).

    Raises:
        GenerationError: If a rule names a path no route contract serves, a
            role the contract does not declare, or an invalid HTTP method.
            Silently dropping any of these is how a contract comes to document
            a protection the deployed app does not apply.
    """
    declared = infra.config.get("protected_routes") or []
    known_roles = {str(r) for r in infra.config.get("roles", [])}
    known_paths = {r.base_path for r in ir.routes if r.base_path}

    resolved: list[tuple[str, list[str], list[str]]] = []
    for rule in declared:
        if not isinstance(rule, dict) or not rule.get("path"):
            raise GenerationError(
                f"{infra.fqn}: every protected_routes entry needs a 'path'; got {rule!r}."
            )
        path = str(rule["path"]).rstrip("/") or "/"
        if path not in known_paths:
            raise GenerationError(
                f"{infra.fqn}: protected_routes declares {path!r}, which no route "
                f"contract serves (known base paths: {sorted(known_paths) or 'none'}). "
                f"The rule could not be enforced at runtime."
            )

        methods = [str(m).upper() for m in rule.get("methods", [])] or sorted(_HTTP_METHODS)
        unknown_methods = sorted(set(methods) - _HTTP_METHODS)
        if unknown_methods:
            raise GenerationError(
                f"{infra.fqn}: protected_routes {path!r} names unknown HTTP "
                f"method(s) {unknown_methods}."
            )

        roles = [str(r) for r in rule.get("roles", [])]
        if not roles:
            raise GenerationError(
                f"{infra.fqn}: protected_routes {path!r} declares no roles. A rule "
                f"that permits every role protects nothing — remove it or name the roles."
            )
        unknown_roles = sorted(set(roles) - known_roles)
        if unknown_roles:
            raise GenerationError(
                f"{infra.fqn}: protected_routes {path!r} requires role(s) "
                f"{unknown_roles}, which are not in config.roles "
                f"({sorted(known_roles) or 'none declared'}). No token could ever "
                f"satisfy the rule."
            )

        resolved.append((path, sorted(set(methods)), sorted(set(roles))))

    return resolved


def _generate_interface(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Auth provider interface")
    content = f"""{header}
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel


class AuthUser(BaseModel):
    id: str
    email: str
    role: str


class TokenPair(BaseModel):
    \"\"\"A short-lived access token and the refresh token that replaces it.\"\"\"

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthProvider(ABC):

    @abstractmethod
    async def authenticate(self, token: str) -> Optional[AuthUser]:
        \"\"\"Return the caller behind an access token, or None if it is not valid.\"\"\"

    @abstractmethod
    async def issue_tokens(self, user: AuthUser) -> TokenPair:
        \"\"\"Mint a new pair. Callers must verify a credential first.\"\"\"

    @abstractmethod
    async def rotate_refresh(self, refresh_token: str) -> Optional[TokenPair]:
        \"\"\"Exchange a refresh token for a new pair, invalidating the old one.\"\"\"
"""
    return GeneratedFile(path="backend/auth/interface.py", content=content, provenance=infra.fqn)


def _generate_token_store(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Refresh-token ledger")
    content = f"""{header}
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from backend.config import DATABASE_BACKEND, DATABASE_URL


class RefreshTokenStore(ABC):
    \"\"\"Records which refresh tokens are still live.

    Rotation needs server-side state: a JWT on its own is valid until it
    expires and cannot be withdrawn, so without this ledger a leaked refresh
    token is a permanent credential and logout is cosmetic.
    \"\"\"

    @abstractmethod
    async def initialize(self) -> None:
        \"\"\"Prepare backing storage. Called once from the app lifespan.\"\"\"

    @abstractmethod
    async def issue(self, jti: str, subject: str, expires_at: datetime) -> None: ...

    @abstractmethod
    async def consume(self, jti: str, subject: str) -> bool:
        \"\"\"Atomically spend a token. False means it was unknown, expired or reused.\"\"\"

    @abstractmethod
    async def revoke_subject(self, subject: str) -> None: ...


class MemoryRefreshTokenStore(RefreshTokenStore):
    \"\"\"Process-local store, paired with DATABASE_BACKEND=memory.

    State dies with the process and is not shared between workers, which is
    acceptable only because the memory backend is a dev/test configuration.
    \"\"\"

    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, datetime]] = {{}}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def issue(self, jti: str, subject: str, expires_at: datetime) -> None:
        async with self._lock:
            self._tokens[jti] = (subject, expires_at)

    async def consume(self, jti: str, subject: str) -> bool:
        async with self._lock:
            entry = self._tokens.pop(jti, None)
        if entry is None:
            return False
        stored_subject, expires_at = entry
        return stored_subject == subject and expires_at > datetime.now(timezone.utc)

    async def revoke_subject(self, subject: str) -> None:
        async with self._lock:
            self._tokens = {{
                jti: entry for jti, entry in self._tokens.items() if entry[0] != subject
            }}


class PostgresRefreshTokenStore(RefreshTokenStore):

    _DDL = \"\"\"
        CREATE TABLE IF NOT EXISTS _auth_refresh_tokens (
            jti         TEXT PRIMARY KEY,
            subject     TEXT NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    \"\"\"
    _INDEX = (
        "CREATE INDEX IF NOT EXISTS _auth_refresh_tokens_subject_idx "
        "ON _auth_refresh_tokens (subject)"
    )

    def __init__(self) -> None:
        self._pool = None
        self._lock = asyncio.Lock()

    async def _get_pool(self):
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    import asyncpg

                    self._pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        return self._pool

    async def initialize(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(self._DDL)
            await conn.execute(self._INDEX)

    async def issue(self, jti: str, subject: str, expires_at: datetime) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO _auth_refresh_tokens (jti, subject, expires_at) "
                "VALUES ($1, $2, $3)",
                jti,
                subject,
                expires_at,
            )

    async def consume(self, jti: str, subject: str) -> bool:
        # DELETE ... RETURNING is the whole point: two concurrent presentations
        # of the same token cannot both come back with a row, so a replay is
        # always distinguishable from the legitimate use.
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "DELETE FROM _auth_refresh_tokens "
                "WHERE jti = $1 AND subject = $2 AND expires_at > now() "
                "RETURNING jti",
                jti,
                subject,
            )
        return row is not None

    async def revoke_subject(self, subject: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM _auth_refresh_tokens WHERE subject = $1", subject)


_store: RefreshTokenStore | None = None


def get_refresh_token_store() -> RefreshTokenStore:
    global _store
    if _store is None:
        _store = (
            PostgresRefreshTokenStore()
            if DATABASE_BACKEND == "postgres"
            else MemoryRefreshTokenStore()
        )
    return _store
"""
    return GeneratedFile(path="backend/auth/token_store.py", content=content, provenance=infra.fqn)


def _generate_jwt_provider(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Built-in JWT auth provider")
    content = f"""{header}
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from backend.auth.interface import AuthProvider, AuthUser, TokenPair
from backend.auth.token_store import get_refresh_token_store
from backend.config import (
    AUTH_AUDIENCE,
    AUTH_ISSUER,
    AUTH_REFRESH_TOKEN_EXPIRE_DAYS,
    AUTH_SECRET,
    AUTH_TOKEN_EXPIRE_MINUTES,
)

# Pinned, and passed to decode() as the only accepted algorithm. Accepting
# whatever the token's own header names is the algorithm-confusion class of
# bug: a "none"-signed or RS256-header token would verify against the HMAC key.
ALGORITHM = "HS256"

_REQUIRED_CLAIMS = ["exp", "iat", "sub", "aud", "iss", "typ"]

_hasher = PasswordHasher()

# Verifying against a real hash when the account does not exist keeps the
# failure cost the same either way, so response timing does not enumerate users.
_ABSENT_ACCOUNT_HASH = _hasher.hash("specora-no-such-account")


class JWTAuthProvider(AuthProvider):

    async def authenticate(self, token: str) -> Optional[AuthUser]:
        payload = self._decode(token, expected_type="access")
        if payload is None:
            return None
        return AuthUser(
            id=str(payload["sub"]),
            email=str(payload.get("email", "")),
            role=str(payload.get("role", "")),
        )

    async def issue_tokens(self, user: AuthUser) -> TokenPair:
        now = datetime.now(timezone.utc)
        access_ttl = timedelta(minutes=AUTH_TOKEN_EXPIRE_MINUTES)
        refresh_ttl = timedelta(days=AUTH_REFRESH_TOKEN_EXPIRE_DAYS)
        claims = {{"sub": user.id, "email": user.email, "role": user.role}}

        refresh_jti = uuid.uuid4().hex
        await get_refresh_token_store().issue(refresh_jti, user.id, now + refresh_ttl)

        return TokenPair(
            access_token=self._encode({{**claims, "typ": "access"}}, now, access_ttl),
            refresh_token=self._encode(
                {{**claims, "typ": "refresh", "jti": refresh_jti}}, now, refresh_ttl
            ),
            expires_in=int(access_ttl.total_seconds()),
        )

    async def rotate_refresh(self, refresh_token: str) -> Optional[TokenPair]:
        payload = self._decode(refresh_token, expected_type="refresh")
        if payload is None or not payload.get("jti"):
            return None

        store = get_refresh_token_store()
        subject = str(payload["sub"])
        if not await store.consume(str(payload["jti"]), subject):
            # The signature was good but the ledger has already spent this jti,
            # so the token was replayed — assume it leaked and drop the whole
            # family rather than issuing the attacker a fresh pair.
            await store.revoke_subject(subject)
            return None

        return await self.issue_tokens(
            AuthUser(
                id=subject,
                email=str(payload.get("email", "")),
                role=str(payload.get("role", "")),
            )
        )

    def _encode(self, claims: dict, now: datetime, ttl: timedelta) -> str:
        payload = {{
            **claims,
            "iss": AUTH_ISSUER,
            "aud": AUTH_AUDIENCE,
            "iat": now,
            "exp": now + ttl,
        }}
        return jwt.encode(payload, AUTH_SECRET, algorithm=ALGORITHM)

    def _decode(self, token: str, *, expected_type: str) -> Optional[dict]:
        try:
            payload = jwt.decode(
                token,
                AUTH_SECRET,
                algorithms=[ALGORITHM],
                audience=AUTH_AUDIENCE,
                issuer=AUTH_ISSUER,
                options={{"require": _REQUIRED_CLAIMS}},
            )
        except jwt.InvalidTokenError:
            return None
        # A refresh token is accepted at exactly one endpoint. Without this it
        # would also open every resource an access token opens, for far longer.
        if payload.get("typ") != expected_type or not payload.get("sub"):
            return None
        return payload


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    try:
        return _hasher.verify(hashed or _ABSENT_ACCOUNT_HASH, plain)
    except (VerificationError, InvalidHashError):
        return False
"""
    return GeneratedFile(path="backend/auth/jwt_provider.py", content=content, provenance=infra.fqn)


def _generate_middleware(ir: DomainIR, infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Auth middleware — FastAPI dependencies")
    rules = protected_routes(ir, infra)

    starlette_imports = [
        "from starlette.requests import Request",
        "from starlette.responses import JSONResponse",
    ] if rules else []

    lines = [
        header,
        "from __future__ import annotations",
        "",
        "from typing import Optional",
        "",
        "from fastapi import Depends, HTTPException",
        "from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer",
        *starlette_imports,
        "",
        "from backend.auth.interface import AuthProvider, AuthUser",
        "from backend.auth.jwt_provider import JWTAuthProvider",
        "",
        "# auto_error=False so this module owns the failure shape: HTTPBearer's own",
        "# error is a 403 with no challenge header, and RFC 7235 requires a 401 to",
        "# carry WWW-Authenticate. It still parses the scheme case-insensitively and",
        "# rejects a bare token with no scheme at all.",
        "_bearer = HTTPBearer(auto_error=False)",
        "",
        f"_CHALLENGE = {{\"WWW-Authenticate\": 'Bearer realm=\"{ir.domain}\"'}}",
        "",
        "_provider: Optional[AuthProvider] = None",
        "",
        "",
        "def get_auth_provider() -> AuthProvider:",
        "    global _provider",
        "    if _provider is None:",
        "        _provider = JWTAuthProvider()",
        "    return _provider",
        "",
        "",
        "async def require_auth(",
        "    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),",
        "    provider: AuthProvider = Depends(get_auth_provider),",
        ") -> AuthUser:",
        "    if credentials is None:",
        '        raise HTTPException(401, detail={"error": "missing_token"}, headers=_CHALLENGE)',
        "    user = await provider.authenticate(credentials.credentials)",
        "    if user is None:",
        '        raise HTTPException(401, detail={"error": "invalid_token"}, headers=_CHALLENGE)',
        "    return user",
        "",
        "",
        "def require_role(*roles: str):",
        "    allowed = frozenset(roles)",
        "",
        "    async def check(user: AuthUser = Depends(require_auth)) -> AuthUser:",
        "        if user.role not in allowed:",
        "            raise HTTPException(",
        '                403, detail={"error": "forbidden", "required_roles": sorted(allowed)}',
        "            )",
        "        return user",
        "",
        "    return check",
        "",
    ]

    if rules:
        lines.extend(_protected_routes_block(rules))

    return GeneratedFile(
        path="backend/auth/middleware.py",
        content="\n".join(lines),
        provenance=infra.fqn,
    )


def _protected_routes_block(rules: list[tuple[str, list[str], list[str]]]) -> list[str]:
    """Emit the app-wide enforcement of `config.protected_routes`."""
    lines = [
        "",
        "# ── Contract-declared route protection ───────────────────────────────",
        "#",
        "# The per-endpoint require_role dependencies cover what a route contract",
        "# annotates. These rules come from the auth contract instead, are declared",
        "# independently of the routes, and are enforced ahead of every handler —",
        "# including any handler that forgets its own dependency.",
        "",
        "PROTECTED_ROUTES: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = (",
    ]
    for path, methods, roles in rules:
        method_set = ", ".join(f'"{m}"' for m in methods)
        role_set = ", ".join(f'"{r}"' for r in roles)
        lines.append(f'    ("{path}", frozenset({{{method_set}}}), frozenset({{{role_set}}})),')
    lines.extend([
        ")",
        "",
        "",
        "def _required_roles(path: str, method: str) -> Optional[frozenset[str]]:",
        "    for prefix, methods, roles in PROTECTED_ROUTES:",
        "        if method in methods and (path == prefix or path.startswith(prefix + \"/\")):",
        "            return roles",
        "    return None",
        "",
        "",
        "async def enforce_protected_routes(request: Request, call_next):",
        "    required = _required_roles(request.url.path, request.method.upper())",
        "    if required is None:",
        "        return await call_next(request)",
        "",
        '    scheme, _, token = request.headers.get("authorization", "").partition(" ")',
        '    if scheme.lower() != "bearer" or not token.strip():',
        "        return JSONResponse(",
        '            status_code=401, content={"error": "missing_token"}, headers=_CHALLENGE',
        "        )",
        "    user = await get_auth_provider().authenticate(token.strip())",
        "    if user is None:",
        "        return JSONResponse(",
        '            status_code=401, content={"error": "invalid_token"}, headers=_CHALLENGE',
        "        )",
        "    if user.role not in required:",
        "        return JSONResponse(",
        "            status_code=403,",
        '            content={"error": "forbidden", "required_roles": sorted(required)},',
        "        )",
        "    return await call_next(request)",
        "",
    ])
    return lines

"""Generate the 12-factor configuration module.

`backend/config.py` is imported before anything else in the generated app, so
it is where a misconfigured container is stopped. Three invariants are checked
at import time rather than at request time, because these images are built by
CI and a forgotten environment variable must not ship as an app that boots
happily with no security:

  - `CORS_ORIGINS` has no default. A browser origin must be named explicitly.
  - `CORS_ORIGINS=*` may never be combined with credentialed requests.
  - `AUTH_SECRET` must differ from the shipped placeholder whenever the domain
    declares an auth contract; otherwise every token in the deployment is
    forgeable by anyone who has read the repository.
"""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile, provenance_header

# The value shipped in .env.example. config.py refuses to boot on it, so the
# two must stay in sync — see gen_docker._generate_env_example.
PLACEHOLDER_SECRET = "change-me-in-production"

# HS256 signs with the raw secret, so anything shorter than the 32-byte hash
# output is worth brute-forcing offline from a single captured token.
MIN_SECRET_LENGTH = 32


def generate_config(ir: DomainIR) -> GeneratedFile:
    """Generate backend/config.py with environment-based configuration."""
    header = provenance_header(
        "python", f"domain/{ir.domain}", "12-factor environment configuration"
    )

    has_auth = any(i.category == "auth" for i in ir.infra)

    lines = [
        header,
        "import os",
        "",
        "",
        "def _flag(name: str, default: str) -> bool:",
        '    return os.getenv(name, default).strip().lower() in ("true", "1", "yes")',
        "",
        "",
        "# Database",
        'DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://specora:specora@localhost:5432/specora")',
        'DATABASE_BACKEND = os.getenv("DATABASE_BACKEND", "postgres")',
        "",
        "# Server",
        'PORT = int(os.getenv("PORT", "8000"))',
        "",
        "# CORS. Empty by default: every browser origin must be named. Reflecting",
        "# an arbitrary Origin back with credentials lets any site issue",
        "# authenticated cross-origin requests on a logged-in user's behalf.",
        'CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]',
        'CORS_ALLOW_CREDENTIALS = _flag("CORS_ALLOW_CREDENTIALS", "true")',
        'if "*" in CORS_ORIGINS and CORS_ALLOW_CREDENTIALS:',
        "    raise RuntimeError(",
        '        "CORS_ORIGINS=* cannot be combined with CORS_ALLOW_CREDENTIALS=true. "',
        '        "List the exact origins that may send credentials, or set "',
        '        "CORS_ALLOW_CREDENTIALS=false."',
        "    )",
        "",
    ]

    if has_auth:
        lines.extend([
            "# Auth",
            "# AUTH_ENABLED is not a kill switch. The domain declares authentication in",
            f"# infra/{ir.domain}/auth, so an image that can turn it off at boot is an",
            "# image that can ship with none.",
            "AUTH_ENABLED = True",
            'if not _flag("AUTH_ENABLED", "true"):',
            "    raise RuntimeError(",
            '        "AUTH_ENABLED=false, but this application\'s contracts declare "',
            '        "authentication. Remove the override; there is no unauthenticated "',
            '        "mode for this build."',
            "    )",
            "",
            'AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "jwt")',
            'AUTH_SECRET = os.getenv("AUTH_SECRET", "")',
            f'if AUTH_SECRET in ("", "{PLACEHOLDER_SECRET}"):',
            "    raise RuntimeError(",
            '        "AUTH_SECRET is unset or still the placeholder shipped in "',
            '        ".env.example. Every JWT this process issues would be forgeable. "',
            "        \"Generate one with: openssl rand -hex 32\"",
            "    )",
            "# RFC 7518 §3.2: an HMAC-SHA256 key shorter than the hash output weakens",
            "# the signature to something worth brute-forcing offline.",
            f"if len(AUTH_SECRET) < {MIN_SECRET_LENGTH}:",
            "    raise RuntimeError(",
            "        f\"AUTH_SECRET is {len(AUTH_SECRET)} characters; "
            f'at least {MIN_SECRET_LENGTH} "',
            "        \"are required. Generate one with: openssl rand -hex 32\"",
            "    )",
            "",
            "# Bound into every token and required on every token it verifies, so a",
            "# token minted for another Specora deployment sharing this secret is not",
            "# accepted here.",
            f'AUTH_ISSUER = os.getenv("AUTH_ISSUER", "specora:{ir.domain}")',
            f'AUTH_AUDIENCE = os.getenv("AUTH_AUDIENCE", "specora:{ir.domain}")',
            "",
            "# Access tokens are short-lived because they cannot be revoked; the",
            "# refresh token is the revocable half of the pair (see auth/token_store).",
            'AUTH_TOKEN_EXPIRE_MINUTES = int(os.getenv("AUTH_TOKEN_EXPIRE_MINUTES", "15"))',
            "AUTH_REFRESH_TOKEN_EXPIRE_DAYS = int(",
            '    os.getenv("AUTH_REFRESH_TOKEN_EXPIRE_DAYS", "14")',
            ")",
            "",
            "# The refresh cookie is Secure by default. Browsers exempt",
            "# http://localhost, so this does not need relaxing for local work — only",
            "# for a deployment deliberately served over plain HTTP.",
            'AUTH_COOKIE_SECURE = _flag("AUTH_COOKIE_SECURE", "true")',
            "",
        ])
    else:
        lines.extend([
            "# Auth — no infra auth contract in this domain, so no auth code is",
            "# generated and nothing reads this beyond diagnostics.",
            "AUTH_ENABLED = False",
            "",
        ])

    return GeneratedFile(
        path="backend/config.py",
        content="\n".join(lines),
        provenance=f"domain/{ir.domain}",
    )

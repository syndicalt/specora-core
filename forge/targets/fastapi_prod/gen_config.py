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

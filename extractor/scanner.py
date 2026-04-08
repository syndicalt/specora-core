"""Pass 1: Discover and classify source files by role."""
from __future__ import annotations

import re
from pathlib import Path

from extractor.models import FileClassification, FileRole

# Directories to skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".egg-info", ".eggs", "htmlcov",
}

# File extensions we care about
EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".sql": "sql",
    ".prisma": "prisma",
}

# Filename patterns for classification
MODEL_PATTERNS = [
    re.compile(r"models?\.py$"),
    re.compile(r"schemas?\.py$"),
    re.compile(r"entities\.py$"),
    re.compile(r"types?\.ts$"),
    re.compile(r".*\.prisma$"),
    re.compile(r".*model.*\.py$", re.IGNORECASE),
    re.compile(r".*schema.*\.py$", re.IGNORECASE),
    re.compile(r".*entity.*\.py$", re.IGNORECASE),
    re.compile(r".*interface.*\.ts$", re.IGNORECASE),
]

ROUTE_PATTERNS = [
    re.compile(r"routes?\.py$"),
    re.compile(r"routers?\.py$"),
    re.compile(r"views?\.py$"),
    re.compile(r"endpoints?\.py$"),
    re.compile(r"controllers?\.py$"),
    re.compile(r"api\.py$"),
    re.compile(r".*routes?.*\.py$", re.IGNORECASE),
    re.compile(r".*controller.*\.ts$", re.IGNORECASE),
]

TEST_PATTERNS = [
    re.compile(r"test_.*\.py$"),
    re.compile(r".*_test\.py$"),
    re.compile(r".*\.test\.ts$"),
    re.compile(r".*\.spec\.ts$"),
    re.compile(r"conftest\.py$"),
]

MIGRATION_PATTERNS = [
    re.compile(r".*\.sql$"),
    re.compile(r".*migration.*\.py$", re.IGNORECASE),
    re.compile(r".*alembic.*\.py$", re.IGNORECASE),
]

CONFIG_PATTERNS = [
    re.compile(r"config.*\.py$", re.IGNORECASE),
    re.compile(r"settings.*\.py$", re.IGNORECASE),
    re.compile(r".*\.config\.ts$"),
    re.compile(r".*\.env.*"),
]

# Content patterns for classification when filename isn't enough
CONTENT_MODEL_HINTS = [
    "BaseModel", "Base = declarative_base", "class Meta:",
    "Column(", "Field(", "interface ", "type ", "@dataclass",
    "TypedDict", "NamedTuple", "Schema",
]

CONTENT_ROUTE_HINTS = [
    "APIRouter", "@app.get", "@app.post", "@router.",
    "Blueprint", "express.Router", "@api_view",
]


def scan_directory(root: Path) -> list[FileClassification]:
    """Scan a directory tree and classify source files by role."""
    results: list[FileClassification] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        # Skip excluded directories
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue

        # Only process known extensions
        ext = path.suffix.lower()
        language = EXTENSIONS.get(ext)
        if not language:
            continue

        rel_path = str(path.relative_to(root))
        size = path.stat().st_size

        # Skip empty files
        if size == 0:
            continue

        role = _classify_file(path, rel_path)

        results.append(FileClassification(
            path=rel_path,
            role=role,
            language=language,
            size_bytes=size,
        ))

    return results


def _classify_file(path: Path, rel_path: str) -> FileRole:
    """Classify a file by its name and path."""
    name = path.name

    # Check test patterns first (highest priority)
    if any(p.search(name) for p in TEST_PATTERNS):
        return FileRole.TEST
    if "test" in rel_path.lower().split("/")[:-1]:  # in a test directory
        return FileRole.TEST

    # Migrations
    if any(p.search(name) for p in MIGRATION_PATTERNS):
        return FileRole.MIGRATION
    if "migration" in rel_path.lower():
        return FileRole.MIGRATION

    # Config
    if any(p.search(name) for p in CONFIG_PATTERNS):
        return FileRole.CONFIG

    # Routes (check before models — some files could match both)
    if any(p.search(name) for p in ROUTE_PATTERNS):
        return FileRole.ROUTE

    # Models
    if any(p.search(name) for p in MODEL_PATTERNS):
        return FileRole.MODEL

    # Fallback: read first 500 bytes for content hints
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:500]
        if any(hint in head for hint in CONTENT_ROUTE_HINTS):
            return FileRole.ROUTE
        if any(hint in head for hint in CONTENT_MODEL_HINTS):
            return FileRole.MODEL
    except OSError:
        pass

    return FileRole.UNKNOWN

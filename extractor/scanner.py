"""Pass 1: Discover and classify source files by role.

Everything under the scan root is hostile input: the Extractor is pointed at a
codebase the user did not write, and in the migration case did not read either.
The walk is therefore bounded on every axis that an attacker or an unlucky repo
controls — path target, file size, tree depth, and file count — and refuses to
read anything whose real path leaves the root.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from extractor.models import FileClassification, FileRole

logger = logging.getLogger(__name__)

# Directories to skip
SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "dist",
    "build",
    "target",
    "vendor",
    ".eggs",
    "htmlcov",
    "site-packages",
}

# `.egg-info` is a suffix, not a whole directory name, so it cannot be matched
# by the exact-name check above.
SKIP_DIR_SUFFIXES = (".egg-info",)

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


@dataclass(frozen=True)
class ScanLimits:
    """Bounds on a single scan.

    Defaults are sized for a source tree, not for whatever happens to be on
    disk: a generated bundle, a checked-in database dump, or a symlink into a
    filesystem with no bottom will otherwise be read into memory in full.
    """

    max_file_bytes: int = 2 * 1024 * 1024
    max_files: int = 20_000
    max_depth: int = 32


# How much of an unclassified file is read for content hints. The hint strings
# all appear in an import or a class header, so more than this buys nothing.
HEAD_BYTES = 4096

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

# Directory names that make everything below them a test file. Matched against
# whole path segments, so `tests/` and `__tests__/` both count and a module
# named `latest.py` does not.
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "testing"}

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
    "BaseModel",
    "Base = declarative_base",
    "class Meta:",
    "Column(",
    "Field(",
    "interface ",
    "type ",
    "@dataclass",
    "TypedDict",
    "NamedTuple",
    "Schema",
]

CONTENT_ROUTE_HINTS = [
    "APIRouter",
    "@app.get",
    "@app.post",
    "@router.",
    "Blueprint",
    "express.Router",
    "@api_view",
]


def scan_directory(
    root: Path,
    *,
    limits: ScanLimits | None = None,
    warnings: list[str] | None = None,
) -> list[FileClassification]:
    """Scan a directory tree and classify source files by role.

    Args:
        root: Directory to walk. Nothing outside its real path is ever read.
        limits: Size/depth/count bounds. Defaults to `ScanLimits()`.
        warnings: Optional sink. Every file the scan declined to look at is
            appended to it with the reason, so a skip is reported rather than
            silently changing what the resulting contracts claim.

    Returns:
        One `FileClassification` per file that was actually read.
    """
    limits = limits or ScanLimits()
    notes = warnings if warnings is not None else []
    results: list[FileClassification] = []

    try:
        real_root = root.resolve(strict=True)
    except OSError as e:
        raise FileNotFoundError(f"cannot scan {root}: {e}") from e

    seen_real: set[Path] = set()
    truncated = False

    for path in _walk(real_root, limits, notes):
        ext = path.suffix.lower()
        language = EXTENSIONS.get(ext)
        if not language:
            continue

        try:
            # `resolve` follows the final symlink; a scanned repo can point one
            # at /etc/passwd or ~/.ssh/id_rsa, and the analyzers would read the
            # target and (on the LLM path) ship it to a third-party API.
            real = path.resolve(strict=True)
            if not real.is_relative_to(real_root):
                notes.append(f"{_rel(path, real_root)}: skipped, symlink escapes the scan root")
                continue
            stat = real.stat()
        except OSError as e:
            notes.append(f"{_rel(path, real_root)}: skipped, cannot stat ({e.strerror or e})")
            continue

        if not stat.st_size:
            continue

        if stat.st_size > limits.max_file_bytes:
            notes.append(
                f"{_rel(path, real_root)}: skipped, {stat.st_size} bytes exceeds the "
                f"{limits.max_file_bytes}-byte limit"
            )
            continue

        # Two names for one file (a symlink beside its target) would otherwise
        # be analyzed twice and merged into a duplicate entity.
        if real in seen_real:
            continue
        seen_real.add(real)

        if len(results) >= limits.max_files:
            truncated = True
            break

        results.append(
            FileClassification(
                path=_rel(path, real_root),
                role=_classify_file(real, _rel(path, real_root), notes),
                language=language,
                size_bytes=stat.st_size,
            )
        )

    if truncated:
        notes.append(
            f"scan stopped at the {limits.max_files}-file limit; the report describes "
            f"only part of {real_root}"
        )

    for note in notes:
        logger.warning("scan: %s", note)

    return sorted(results, key=lambda f: f.path)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _walk(root: Path, limits: ScanLimits, notes: list[str]):
    """Yield files under `root`, depth-bounded and without following symlinks.

    `Path.rglob` gives no control over depth and no way to report an unreadable
    directory, so the walk is explicit.
    """
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError as e:
            notes.append(f"{_rel(directory, root)}: skipped, cannot list ({e.strerror or e})")
            continue

        for entry in entries:
            if entry.is_symlink() and entry.is_dir():
                # A symlinked directory is either a loop or a path out of the
                # tree; neither is part of the codebase being described.
                notes.append(f"{_rel(entry, root)}: skipped, symlinked directory not followed")
                continue
            if entry.is_dir():
                name = entry.name
                if name in SKIP_DIRS or name.endswith(SKIP_DIR_SUFFIXES):
                    continue
                if depth + 1 > limits.max_depth:
                    notes.append(
                        f"{_rel(entry, root)}: skipped, deeper than the "
                        f"{limits.max_depth}-directory limit"
                    )
                    continue
                stack.append((entry, depth + 1))
            elif entry.is_file():
                yield entry


def _classify_file(path: Path, rel_path: str, notes: list[str]) -> FileRole:
    """Classify a file by its name, path, and — as a fallback — its first bytes."""
    name = path.name
    segments = [s.lower() for s in Path(rel_path).parts[:-1]]

    # Check test patterns first (highest priority)
    if any(p.search(name) for p in TEST_PATTERNS):
        return FileRole.TEST
    if any(s in TEST_DIR_NAMES for s in segments):
        return FileRole.TEST

    # Migrations
    if any(p.search(name) for p in MIGRATION_PATTERNS):
        return FileRole.MIGRATION
    if any("migration" in s for s in segments):
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

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            head = fh.read(HEAD_BYTES)
    except OSError as e:
        notes.append(f"{rel_path}: classified as unknown, cannot read ({e.strerror or e})")
        return FileRole.UNKNOWN

    if any(hint in head for hint in CONTENT_ROUTE_HINTS):
        return FileRole.ROUTE
    if any(hint in head for hint in CONTENT_MODEL_HINTS):
        return FileRole.MODEL

    return FileRole.UNKNOWN

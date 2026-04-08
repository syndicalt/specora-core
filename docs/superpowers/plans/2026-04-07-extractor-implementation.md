# Extractor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Extractor (Tier 5) — reverse-engineer existing Python/TypeScript codebases into Specora contracts via multi-pass LLM analysis.

**Architecture:** Four-pass pipeline: scan files → classify by role → LLM extracts entities/routes/workflows per file group → cross-reference and synthesize into an AnalysisReport → user reviews → emit contracts via Factory emitters. Non-destructive — only writes to `domains/`.

**Tech Stack:** Python 3.10+, existing `engine.engine.LLMEngine`, existing Factory emitters (`emit_entity`, `emit_route`, `emit_page`, `emit_workflow`), Rich for the report UI, Click for CLI.

**Issue:** syndicalt/specora-core#5

---

## File Map

| File | Responsibility |
|------|---------------|
| `extractor/__init__.py` | Package init |
| `extractor/models.py` | AnalysisReport, ExtractedEntity, ExtractedRoute, ExtractedWorkflow, FileClassification |
| `extractor/scanner.py` | Pass 1: discover + classify source files by role |
| `extractor/analyzers/__init__.py` | Package init |
| `extractor/analyzers/python_models.py` | Pass 2a: LLM extracts entities from Python model files |
| `extractor/analyzers/typescript_types.py` | Pass 2b: LLM extracts entities from TypeScript type files |
| `extractor/analyzers/routes.py` | Pass 2c: LLM extracts routes from any framework |
| `extractor/cross_ref.py` | Pass 3: resolve relationships, detect workflows |
| `extractor/synthesizer.py` | Pass 4: merge extractions into unified AnalysisReport |
| `extractor/reporter.py` | Rich-formatted report with accept/edit/skip per entity |
| `extractor/emitter.py` | AnalysisReport → contract YAML via Factory emitters |
| `extractor/cli/__init__.py` | Package init |
| `extractor/cli/commands.py` | `spc extract <path>` CLI command |
| `tests/test_extractor/__init__.py` | Package init |
| `tests/test_extractor/test_models.py` | Model tests |
| `tests/test_extractor/test_scanner.py` | Scanner tests |
| `tests/test_extractor/test_emitter.py` | Emitter tests |
| `tests/test_extractor/test_pipeline.py` | End-to-end pipeline test |

---

### Task 1: Data Models

**Files:**
- Create: `extractor/__init__.py`
- Create: `extractor/models.py`
- Create: `extractor/analyzers/__init__.py`
- Create: `extractor/cli/__init__.py`
- Create: `tests/test_extractor/__init__.py`
- Create: `tests/test_extractor/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extractor/test_models.py
"""Tests for extractor.models — data structures for codebase analysis."""
import pytest

from extractor.models import (
    AnalysisReport,
    Confidence,
    ExtractedEntity,
    ExtractedField,
    ExtractedRoute,
    ExtractedWorkflow,
    FileClassification,
    FileRole,
)


class TestFileClassification:

    def test_create(self) -> None:
        fc = FileClassification(path="models.py", role=FileRole.MODEL, language="python")
        assert fc.path == "models.py"
        assert fc.role == FileRole.MODEL
        assert fc.language == "python"


class TestExtractedEntity:

    def test_create_minimal(self) -> None:
        entity = ExtractedEntity(
            name="User",
            source_file="models.py",
            fields=[ExtractedField(name="email", type="string", required=True)],
        )
        assert entity.name == "User"
        assert len(entity.fields) == 1
        assert entity.confidence == Confidence.HIGH

    def test_to_emitter_data(self) -> None:
        entity = ExtractedEntity(
            name="Book",
            source_file="models.py",
            description="A library book",
            fields=[
                ExtractedField(name="title", type="string", required=True, description="Book title"),
                ExtractedField(name="isbn", type="string"),
            ],
        )
        data = entity.to_emitter_data()
        assert data["description"] == "A library book"
        assert "title" in data["fields"]
        assert data["fields"]["title"]["type"] == "string"
        assert data["fields"]["title"]["required"] is True
        assert "mixin/stdlib/timestamped" in data["mixins"]


class TestExtractedRoute:

    def test_create(self) -> None:
        route = ExtractedRoute(
            path="/api/users",
            method="GET",
            entity_name="user",
            source_file="routes.py",
        )
        assert route.path == "/api/users"


class TestExtractedWorkflow:

    def test_create(self) -> None:
        wf = ExtractedWorkflow(
            name="order_lifecycle",
            entity_name="order",
            states=["pending", "confirmed", "shipped", "delivered"],
            initial="pending",
            source_file="models.py",
        )
        assert wf.initial == "pending"
        assert len(wf.states) == 4

    def test_to_emitter_data(self) -> None:
        wf = ExtractedWorkflow(
            name="order_lifecycle",
            entity_name="order",
            states=["pending", "shipped", "delivered"],
            initial="pending",
            transitions=[
                {"from": "pending", "to": "shipped"},
                {"from": "shipped", "to": "delivered"},
            ],
            source_file="models.py",
        )
        data = wf.to_emitter_data()
        assert data["initial"] == "pending"
        assert len(data["states"]) == 3
        assert len(data["transitions"]) == 2


class TestAnalysisReport:

    def test_create_empty(self) -> None:
        report = AnalysisReport(domain="test")
        assert report.domain == "test"
        assert len(report.entities) == 0

    def test_summary(self) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(name="Product", source_file="m.py", fields=[]),
                ExtractedEntity(name="Order", source_file="m.py", fields=[]),
            ],
            routes=[ExtractedRoute(path="/products", method="GET", entity_name="product", source_file="r.py")],
            workflows=[ExtractedWorkflow(name="order_lifecycle", entity_name="order", states=["new", "done"], initial="new", source_file="m.py")],
        )
        s = report.summary()
        assert "2 entities" in s
        assert "1 route" in s
        assert "1 workflow" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extractor/test_models.py -v`

- [ ] **Step 3: Implement models**

```python
# extractor/__init__.py
# (empty)

# extractor/analyzers/__init__.py
# (empty)

# extractor/cli/__init__.py
# (empty)

# tests/test_extractor/__init__.py
# (empty)
```

```python
# extractor/models.py
"""Data models for codebase analysis and extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FileRole(str, Enum):
    MODEL = "model"
    ROUTE = "route"
    PAGE = "page"
    MIGRATION = "migration"
    CONFIG = "config"
    TEST = "test"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class FileClassification:
    path: str
    role: FileRole
    language: str
    size_bytes: int = 0


@dataclass
class ExtractedField:
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    enum_values: list[str] = field(default_factory=list)
    reference_entity: str = ""
    reference_display: str = "name"
    reference_edge: str = ""


@dataclass
class ExtractedEntity:
    name: str
    source_file: str
    fields: list[ExtractedField] = field(default_factory=list)
    description: str = ""
    confidence: Confidence = Confidence.HIGH
    mixins: list[str] = field(default_factory=list)
    has_timestamps: bool = True
    state_field: str = ""
    state_values: list[str] = field(default_factory=list)

    def to_emitter_data(self) -> dict:
        """Convert to the dict format expected by emit_entity()."""
        fields: dict[str, dict[str, Any]] = {}
        for f in self.fields:
            fd: dict[str, Any] = {"type": f.type}
            if f.required:
                fd["required"] = True
            if f.description:
                fd["description"] = f.description
            if f.enum_values:
                fd["enum"] = f.enum_values
            if f.reference_entity:
                fd["references"] = {
                    "entity": f.reference_entity,
                    "display": f.reference_display,
                    "graph_edge": f.reference_edge or f.name.upper().replace("_ID", ""),
                }
            fields[f.name] = fd

        mixins = list(self.mixins) if self.mixins else []
        if not mixins:
            mixins = ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"]

        return {
            "description": self.description or f"A {self.name} entity",
            "fields": fields,
            "mixins": mixins,
        }


@dataclass
class ExtractedRoute:
    path: str
    method: str
    entity_name: str
    source_file: str
    summary: str = ""
    confidence: Confidence = Confidence.HIGH


@dataclass
class ExtractedWorkflow:
    name: str
    entity_name: str
    states: list[str]
    initial: str
    source_file: str
    transitions: list[dict] = field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM

    def to_emitter_data(self) -> dict:
        """Convert to the dict format expected by emit_workflow()."""
        states = {}
        for s in self.states:
            states[s] = {"label": s.replace("_", " ").title()}

        transitions = self.transitions if self.transitions else []
        if not transitions and len(self.states) > 1:
            for i in range(len(self.states) - 1):
                transitions.append({"from": self.states[i], "to": self.states[i + 1]})

        return {
            "initial": self.initial,
            "states": states,
            "transitions": transitions,
            "description": f"{self.entity_name} lifecycle",
        }


@dataclass
class AnalysisReport:
    domain: str
    entities: list[ExtractedEntity] = field(default_factory=list)
    routes: list[ExtractedRoute] = field(default_factory=list)
    workflows: list[ExtractedWorkflow] = field(default_factory=list)
    files_scanned: int = 0
    files_analyzed: int = 0

    def summary(self) -> str:
        parts = []
        if self.entities:
            parts.append(f"{len(self.entities)} entities")
        if self.routes:
            parts.append(f"{len(self.routes)} routes")
        if self.workflows:
            parts.append(f"{len(self.workflows)} workflows")
        return ", ".join(parts) if parts else "nothing found"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_extractor/test_models.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add extractor/ tests/test_extractor/
git commit -m "feat(#5/T1): extractor data models — AnalysisReport, ExtractedEntity, etc."
```

---

### Task 2: File Scanner

**Files:**
- Create: `extractor/scanner.py`
- Create: `tests/test_extractor/test_scanner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extractor/test_scanner.py
"""Tests for extractor.scanner — file discovery and classification."""
from pathlib import Path

import pytest

from extractor.models import FileRole
from extractor.scanner import scan_directory


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a minimal project structure."""
    # Python models
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text(
        "from pydantic import BaseModel\nclass User(BaseModel):\n    name: str\n", encoding="utf-8"
    )
    (tmp_path / "app" / "schemas.py").write_text(
        "from sqlalchemy import Column\nclass UserTable:\n    pass\n", encoding="utf-8"
    )
    # Routes
    (tmp_path / "app" / "routes.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/users')\ndef list_users(): pass\n", encoding="utf-8"
    )
    # Tests (should be skipped)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_user.py").write_text("def test_user(): pass\n", encoding="utf-8")
    # Config
    (tmp_path / "config.py").write_text("DATABASE_URL = 'postgres://'\n", encoding="utf-8")
    # TypeScript
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "types.ts").write_text("export interface User { name: string; }\n", encoding="utf-8")
    # Migration
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001_init.sql").write_text("CREATE TABLE users (id INT);\n", encoding="utf-8")
    return tmp_path


class TestScanDirectory:

    def test_finds_python_models(self, sample_project: Path) -> None:
        results = scan_directory(sample_project)
        models = [f for f in results if f.role == FileRole.MODEL]
        assert len(models) >= 1
        assert any("models.py" in f.path for f in models)

    def test_finds_routes(self, sample_project: Path) -> None:
        results = scan_directory(sample_project)
        routes = [f for f in results if f.role == FileRole.ROUTE]
        assert len(routes) >= 1

    def test_classifies_tests(self, sample_project: Path) -> None:
        results = scan_directory(sample_project)
        tests = [f for f in results if f.role == FileRole.TEST]
        assert len(tests) >= 1

    def test_detects_languages(self, sample_project: Path) -> None:
        results = scan_directory(sample_project)
        languages = {f.language for f in results}
        assert "python" in languages
        assert "typescript" in languages

    def test_returns_file_sizes(self, sample_project: Path) -> None:
        results = scan_directory(sample_project)
        assert all(f.size_bytes > 0 for f in results)
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement scanner**

```python
# extractor/scanner.py
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
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_extractor/test_scanner.py -v`

- [ ] **Step 5: Commit**

```bash
git add extractor/scanner.py tests/test_extractor/test_scanner.py
git commit -m "feat(#5/T2): file scanner — discover and classify source files by role"
```

---

### Task 3: Python Model Analyzer

**Files:**
- Create: `extractor/analyzers/python_models.py`

- [ ] **Step 1: Implement analyzer**

```python
# extractor/analyzers/python_models.py
"""Pass 2a: Extract entities from Python model files via LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from extractor.models import Confidence, ExtractedEntity, ExtractedField

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a code analysis expert. You read Python model/schema files and extract data models.

For each class/model found, output a JSON array of entities:

```json
[
  {
    "name": "User",
    "description": "A user account",
    "fields": [
      {"name": "email", "type": "email", "required": true, "description": "User email"},
      {"name": "name", "type": "string", "required": true},
      {"name": "role", "type": "string", "enum_values": ["admin", "user", "guest"]},
      {"name": "department_id", "type": "uuid", "reference_entity": "Department", "reference_edge": "BELONGS_TO"}
    ],
    "state_field": "status",
    "state_values": ["active", "inactive", "suspended"]
  }
]
```

Type mapping:
- str/String/VARCHAR/Text → "string" (short) or "text" (long content)
- int/Integer/BigInteger → "integer"
- float/Decimal/Numeric → "number"
- bool/Boolean → "boolean"
- datetime/DateTime/timestamp → "datetime"
- date/Date → "date"
- UUID/uuid → "uuid"
- EmailStr → "email"
- list/List/Array → "array"
- dict/Dict/JSON/JSONB → "object"

Reference detection:
- Fields ending in _id that reference another model → set reference_entity to the target model name
- Set reference_edge to SCREAMING_SNAKE (e.g., BELONGS_TO, CREATED_BY)

State machine detection:
- Enum fields with lifecycle-like values (active/inactive, open/closed, pending/approved) → set state_field and state_values

Only output the JSON array. No other text.
"""


def analyze_python_models(
    file_paths: list[str],
    root: Path,
) -> list[ExtractedEntity]:
    """Analyze Python model files and extract entities.

    Reads each file, sends to LLM, parses the structured response.
    Falls back to basic regex extraction if LLM is unavailable.
    """
    all_entities: list[ExtractedEntity] = []

    # Batch files together if small enough (< 8000 chars total)
    batches = _batch_files(file_paths, root, max_chars=8000)

    for batch_paths, batch_content in batches:
        entities = _extract_via_llm(batch_content, batch_paths)
        if entities is None:
            entities = _extract_via_regex(batch_paths, root)
        all_entities.extend(entities)

    return all_entities


def _batch_files(file_paths: list[str], root: Path, max_chars: int) -> list[tuple[list[str], str]]:
    """Group files into batches that fit within the char limit."""
    batches = []
    current_paths: list[str] = []
    current_content = ""

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        header = f"\n# === {fp} ===\n"
        if len(current_content) + len(header) + len(content) > max_chars and current_paths:
            batches.append((current_paths, current_content))
            current_paths = []
            current_content = ""

        current_paths.append(fp)
        current_content += header + content

    if current_paths:
        batches.append((current_paths, current_content))

    return batches


def _extract_via_llm(content: str, source_files: list[str]) -> Optional[list[ExtractedEntity]]:
    """Use LLM to extract entities from Python code."""
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception as e:
        logger.warning("LLM not available: %s", e)
        return None

    prompt = f"Analyze these Python files and extract all data models:\n\n```python\n{content}\n```"

    try:
        response = engine.ask(question=prompt, system=SYSTEM_PROMPT)
    except Exception as e:
        logger.error("LLM request failed: %s", e)
        return None

    return _parse_llm_response(response, source_files)


def _parse_llm_response(response: str, source_files: list[str]) -> Optional[list[ExtractedEntity]]:
    """Parse the LLM's JSON response into ExtractedEntity objects."""
    # Extract JSON from code block or raw response
    match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    raw = match.group(1) if match else response

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse LLM response as JSON")
        return None

    if not isinstance(data, list):
        data = [data]

    entities = []
    source = source_files[0] if source_files else "unknown"

    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            continue

        fields = []
        for f in item.get("fields", []):
            fields.append(ExtractedField(
                name=f.get("name", ""),
                type=f.get("type", "string"),
                required=f.get("required", False),
                description=f.get("description", ""),
                enum_values=f.get("enum_values", []),
                reference_entity=f.get("reference_entity", ""),
                reference_display=f.get("reference_display", "name"),
                reference_edge=f.get("reference_edge", ""),
            ))

        entities.append(ExtractedEntity(
            name=item["name"],
            source_file=source,
            fields=fields,
            description=item.get("description", ""),
            confidence=Confidence.HIGH,
            state_field=item.get("state_field", ""),
            state_values=item.get("state_values", []),
        ))

    return entities


def _extract_via_regex(file_paths: list[str], root: Path) -> list[ExtractedEntity]:
    """Fallback: basic regex extraction from Python files."""
    entities = []
    class_pattern = re.compile(r"class\s+(\w+)\s*\(.*(?:BaseModel|Base|Model|db\.Model)")

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for match in class_pattern.finditer(content):
            name = match.group(1)
            entities.append(ExtractedEntity(
                name=name,
                source_file=fp,
                fields=[],
                description=f"Extracted from {fp}",
                confidence=Confidence.LOW,
            ))

    return entities
```

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -q`

- [ ] **Step 3: Commit**

```bash
git add extractor/analyzers/python_models.py
git commit -m "feat(#5/T3): Python model analyzer — LLM extracts entities from Pydantic/SQLAlchemy/Django"
```

---

### Task 4: TypeScript Type Analyzer

**Files:**
- Create: `extractor/analyzers/typescript_types.py`

- [ ] **Step 1: Implement analyzer**

```python
# extractor/analyzers/typescript_types.py
"""Pass 2b: Extract entities from TypeScript type files via LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from extractor.models import Confidence, ExtractedEntity, ExtractedField

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a code analysis expert. You read TypeScript/JavaScript type files and extract data models.

For each interface/type/class found, output a JSON array:

```json
[
  {
    "name": "User",
    "description": "A user account",
    "fields": [
      {"name": "email", "type": "email", "required": true},
      {"name": "name", "type": "string", "required": true},
      {"name": "role", "type": "string", "enum_values": ["admin", "user"]},
      {"name": "departmentId", "type": "uuid", "reference_entity": "Department", "reference_edge": "BELONGS_TO"}
    ],
    "state_field": "status",
    "state_values": ["active", "inactive"]
  }
]
```

Type mapping:
- string → "string" or "text" (if long content)
- number → "number" or "integer" (if whole numbers)
- boolean → "boolean"
- Date → "datetime"
- string[] / Array<string> → "array"
- Record<string, any> / object → "object"
- Fields with "email" in name → "email"
- Fields with "id"/"Id"/"ID" suffix → "uuid"

Only output the JSON array. No other text.
"""


def analyze_typescript_types(
    file_paths: list[str],
    root: Path,
) -> list[ExtractedEntity]:
    """Analyze TypeScript type files and extract entities."""
    all_entities: list[ExtractedEntity] = []

    batches = _batch_files(file_paths, root, max_chars=8000)

    for batch_paths, batch_content in batches:
        entities = _extract_via_llm(batch_content, batch_paths)
        if entities is None:
            entities = _extract_via_regex(batch_paths, root)
        all_entities.extend(entities)

    return all_entities


def _batch_files(file_paths: list[str], root: Path, max_chars: int) -> list[tuple[list[str], str]]:
    batches = []
    current_paths: list[str] = []
    current_content = ""

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        header = f"\n// === {fp} ===\n"
        if len(current_content) + len(header) + len(content) > max_chars and current_paths:
            batches.append((current_paths, current_content))
            current_paths = []
            current_content = ""

        current_paths.append(fp)
        current_content += header + content

    if current_paths:
        batches.append((current_paths, current_content))

    return batches


def _extract_via_llm(content: str, source_files: list[str]) -> Optional[list[ExtractedEntity]]:
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception:
        return None

    prompt = f"Analyze these TypeScript files and extract all data models:\n\n```typescript\n{content}\n```"

    try:
        response = engine.ask(question=prompt, system=SYSTEM_PROMPT)
    except Exception:
        return None

    return _parse_response(response, source_files)


def _parse_response(response: str, source_files: list[str]) -> Optional[list[ExtractedEntity]]:
    match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    raw = match.group(1) if match else response

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        data = [data]

    entities = []
    source = source_files[0] if source_files else "unknown"

    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            continue

        fields = []
        for f in item.get("fields", []):
            fields.append(ExtractedField(
                name=f.get("name", ""),
                type=f.get("type", "string"),
                required=f.get("required", False),
                description=f.get("description", ""),
                enum_values=f.get("enum_values", []),
                reference_entity=f.get("reference_entity", ""),
                reference_edge=f.get("reference_edge", ""),
            ))

        entities.append(ExtractedEntity(
            name=item["name"],
            source_file=source,
            fields=fields,
            description=item.get("description", ""),
            confidence=Confidence.HIGH,
            state_field=item.get("state_field", ""),
            state_values=item.get("state_values", []),
        ))

    return entities


def _extract_via_regex(file_paths: list[str], root: Path) -> list[ExtractedEntity]:
    entities = []
    patterns = [
        re.compile(r"(?:export\s+)?interface\s+(\w+)"),
        re.compile(r"(?:export\s+)?type\s+(\w+)\s*="),
        re.compile(r"(?:export\s+)?class\s+(\w+)"),
    ]

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for pattern in patterns:
            for match in pattern.finditer(content):
                name = match.group(1)
                if name[0].isupper():
                    entities.append(ExtractedEntity(
                        name=name,
                        source_file=fp,
                        fields=[],
                        confidence=Confidence.LOW,
                    ))

    return entities
```

- [ ] **Step 2: Commit**

```bash
git add extractor/analyzers/typescript_types.py
git commit -m "feat(#5/T4): TypeScript type analyzer — LLM extracts entities from interfaces/types/Prisma"
```

---

### Task 5: Route Analyzer

**Files:**
- Create: `extractor/analyzers/routes.py`

- [ ] **Step 1: Implement route analyzer**

```python
# extractor/analyzers/routes.py
"""Pass 2c: Extract API routes from any framework via LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from extractor.models import Confidence, ExtractedRoute

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a code analysis expert. You read route/controller/view files and extract API endpoints.

For each endpoint, output a JSON array:

```json
[
  {"path": "/api/users", "method": "GET", "entity_name": "user", "summary": "List all users"},
  {"path": "/api/users", "method": "POST", "entity_name": "user", "summary": "Create a user"},
  {"path": "/api/users/{id}", "method": "GET", "entity_name": "user", "summary": "Get user by ID"},
  {"path": "/api/users/{id}", "method": "PATCH", "entity_name": "user", "summary": "Update a user"},
  {"path": "/api/users/{id}", "method": "DELETE", "entity_name": "user", "summary": "Delete a user"}
]
```

entity_name should be the singular snake_case name of the resource (user, not users).
Only output the JSON array. No other text.
"""


def analyze_routes(
    file_paths: list[str],
    root: Path,
) -> list[ExtractedRoute]:
    """Analyze route files and extract API endpoints."""
    all_routes: list[ExtractedRoute] = []

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        routes = _extract_via_llm(content, fp)
        if routes is None:
            routes = _extract_via_regex(content, fp)
        all_routes.extend(routes)

    return all_routes


def _extract_via_llm(content: str, source_file: str) -> Optional[list[ExtractedRoute]]:
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception:
        return None

    prompt = f"Extract all API endpoints from this file:\n\n```\n{content[:6000]}\n```"

    try:
        response = engine.ask(question=prompt, system=SYSTEM_PROMPT)
    except Exception:
        return None

    match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    raw = match.group(1) if match else response

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        data = [data]

    routes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        routes.append(ExtractedRoute(
            path=item.get("path", ""),
            method=item.get("method", "GET").upper(),
            entity_name=item.get("entity_name", ""),
            source_file=source_file,
            summary=item.get("summary", ""),
            confidence=Confidence.HIGH,
        ))

    return routes


def _extract_via_regex(content: str, source_file: str) -> list[ExtractedRoute]:
    """Fallback: basic regex extraction of routes."""
    routes = []
    patterns = [
        re.compile(r'@(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']'),
        re.compile(r'@api_view\s*\(\s*\[([^\]]+)\]\s*\)'),
        re.compile(r'\.(?:get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']'),
    ]

    for pattern in patterns:
        for match in pattern.finditer(content):
            if len(match.groups()) == 2:
                method, path = match.group(1).upper(), match.group(2)
            else:
                method, path = "GET", match.group(1)

            entity = path.strip("/").split("/")[0].rstrip("s") if "/" in path else ""
            routes.append(ExtractedRoute(
                path=path,
                method=method,
                entity_name=entity,
                source_file=source_file,
                confidence=Confidence.LOW,
            ))

    return routes
```

- [ ] **Step 2: Commit**

```bash
git add extractor/analyzers/routes.py
git commit -m "feat(#5/T5): route analyzer — LLM extracts API endpoints from FastAPI/Express/Django"
```

---

### Task 6: Cross-Reference + Workflow Detection

**Files:**
- Create: `extractor/cross_ref.py`

- [ ] **Step 1: Implement cross-reference**

```python
# extractor/cross_ref.py
"""Pass 3: Resolve relationships and detect workflows."""
from __future__ import annotations

from extractor.models import (
    Confidence,
    ExtractedEntity,
    ExtractedRoute,
    ExtractedWorkflow,
)
from forge.normalize import normalize_name


def cross_reference(
    entities: list[ExtractedEntity],
    routes: list[ExtractedRoute],
    domain: str,
) -> tuple[list[ExtractedEntity], list[ExtractedRoute], list[ExtractedWorkflow]]:
    """Resolve relationships between extracted entities, routes, and workflows.

    1. Normalize entity names to snake_case
    2. Resolve reference_entity fields to FQNs
    3. Detect workflows from state fields
    4. Match routes to entities
    """
    entity_names = {normalize_name(e.name): e for e in entities}
    workflows: list[ExtractedWorkflow] = []

    # Normalize entity names and resolve references
    for entity in entities:
        entity.name = normalize_name(entity.name)

        for field in entity.fields:
            if field.reference_entity:
                ref_name = normalize_name(field.reference_entity)
                if ref_name in entity_names:
                    field.reference_entity = f"entity/{domain}/{ref_name}"
                else:
                    field.reference_entity = f"entity/{domain}/{ref_name}"

                if not field.reference_edge:
                    field.reference_edge = field.name.upper().replace("_ID", "")

        # Detect workflows from state fields
        if entity.state_field and entity.state_values and len(entity.state_values) >= 2:
            wf_name = f"{entity.name}_lifecycle"
            workflows.append(ExtractedWorkflow(
                name=wf_name,
                entity_name=entity.name,
                states=entity.state_values,
                initial=entity.state_values[0],
                source_file=entity.source_file,
                confidence=Confidence.MEDIUM,
            ))

    # Match routes to entities
    for route in routes:
        if route.entity_name:
            route.entity_name = normalize_name(route.entity_name)

    return entities, routes, workflows
```

- [ ] **Step 2: Commit**

```bash
git add extractor/cross_ref.py
git commit -m "feat(#5/T6): cross-reference — resolve relationships, detect workflows"
```

---

### Task 7: Synthesizer

**Files:**
- Create: `extractor/synthesizer.py`

- [ ] **Step 1: Implement synthesizer**

```python
# extractor/synthesizer.py
"""Pass 4: Merge all extractions into a unified AnalysisReport."""
from __future__ import annotations

from pathlib import Path

from extractor.analyzers.python_models import analyze_python_models
from extractor.analyzers.typescript_types import analyze_typescript_types
from extractor.analyzers.routes import analyze_routes
from extractor.cross_ref import cross_reference
from extractor.models import AnalysisReport, FileClassification, FileRole
from extractor.scanner import scan_directory


def synthesize(source_path: Path, domain: str) -> AnalysisReport:
    """Run the full 4-pass extraction pipeline.

    Pass 1: Scan and classify files
    Pass 2: Extract entities from model files, routes from route files
    Pass 3: Cross-reference and detect workflows
    Pass 4: Build the AnalysisReport
    """
    # Pass 1: Scan
    files = scan_directory(source_path)

    # Group by role and language
    python_models = [f.path for f in files if f.role == FileRole.MODEL and f.language == "python"]
    ts_models = [f.path for f in files if f.role == FileRole.MODEL and f.language == "typescript"]
    route_files = [f.path for f in files if f.role == FileRole.ROUTE]

    # Pass 2: Extract
    entities = []
    if python_models:
        entities.extend(analyze_python_models(python_models, source_path))
    if ts_models:
        entities.extend(analyze_typescript_types(ts_models, source_path))

    routes = []
    if route_files:
        routes = analyze_routes(route_files, source_path)

    # Pass 3: Cross-reference
    entities, routes, workflows = cross_reference(entities, routes, domain)

    # Deduplicate entities by name
    seen: dict[str, int] = {}
    unique_entities = []
    for e in entities:
        if e.name not in seen:
            seen[e.name] = len(unique_entities)
            unique_entities.append(e)
        else:
            # Merge fields from duplicate into existing
            existing = unique_entities[seen[e.name]]
            existing_field_names = {f.name for f in existing.fields}
            for f in e.fields:
                if f.name not in existing_field_names:
                    existing.fields.append(f)

    # Pass 4: Build report
    return AnalysisReport(
        domain=domain,
        entities=unique_entities,
        routes=routes,
        workflows=workflows,
        files_scanned=len(files),
        files_analyzed=len(python_models) + len(ts_models) + len(route_files),
    )
```

- [ ] **Step 2: Commit**

```bash
git add extractor/synthesizer.py
git commit -m "feat(#5/T7): synthesizer — merge extractions into unified AnalysisReport"
```

---

### Task 8: Reporter

**Files:**
- Create: `extractor/reporter.py`

- [ ] **Step 1: Implement reporter**

```python
# extractor/reporter.py
"""Rich-formatted analysis report with accept/edit/skip per entity."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from extractor.models import AnalysisReport, Confidence, ExtractedEntity

console = Console()


def display_report(report: AnalysisReport) -> list[ExtractedEntity]:
    """Display the analysis report and let the user accept/skip each entity.

    Returns the list of accepted entities.
    """
    console.print()
    console.print(Rule("[bold magenta]Extraction Report[/bold magenta]", style="magenta"))
    console.print()

    # Summary table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Domain", f"[cyan]{report.domain}[/cyan]")
    table.add_row("Files scanned", str(report.files_scanned))
    table.add_row("Files analyzed", str(report.files_analyzed))
    table.add_row("Entities found", f"[green]{len(report.entities)}[/green]")
    table.add_row("Routes found", str(len(report.routes)))
    table.add_row("Workflows detected", str(len(report.workflows)))
    console.print(table)
    console.print()

    if not report.entities:
        console.print("  [yellow]No entities found.[/yellow]")
        return []

    console.print(Rule("[bold]Review Entities[/bold]", style="dim"))
    console.print()

    accepted: list[ExtractedEntity] = []

    for i, entity in enumerate(report.entities, 1):
        confidence_color = {"high": "green", "medium": "yellow", "low": "red"}.get(entity.confidence.value, "white")

        # Entity header
        console.print(f"  [bold]{i}/{len(report.entities)}[/bold]  [bold cyan]{entity.name}[/bold cyan]  [{confidence_color}]{entity.confidence.value} confidence[/{confidence_color}]")
        if entity.description:
            console.print(f"  [dim]{entity.description}[/dim]")
        console.print(f"  [dim]Source: {entity.source_file}[/dim]")

        # Fields table
        if entity.fields:
            ft = Table(show_header=True, box=None, padding=(0, 1), show_edge=False)
            ft.add_column("Field", style="cyan", min_width=16)
            ft.add_column("Type", min_width=10)
            ft.add_column("Req", justify="center", min_width=3)
            ft.add_column("Details", style="dim")

            for f in entity.fields:
                req = "✓" if f.required else ""
                details = []
                if f.enum_values:
                    details.append(f"enum: {', '.join(f.enum_values[:4])}")
                if f.reference_entity:
                    details.append(f"→ {f.reference_entity}")
                ft.add_row(f.name, f.type, req, " | ".join(details))

            console.print(ft)

        # State machine
        if entity.state_field:
            states = " → ".join(entity.state_values)
            console.print(f"  [dim]State machine: {entity.state_field} ({states})[/dim]")

        console.print()

        # Accept/Skip
        try:
            response = console.input("  [bold][A]ccept / [S]kip? [/bold]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n  [dim]Cancelled.[/dim]")
            return accepted

        if response in ("", "a", "accept", "y", "yes"):
            accepted.append(entity)
            console.print("  [green]✓ Accepted[/green]")
        else:
            console.print("  [yellow]— Skipped[/yellow]")

        console.print()

    console.print(Rule(style="dim"))
    console.print(f"  [bold]{len(accepted)}/{len(report.entities)} entities accepted[/bold]")
    console.print()

    return accepted
```

- [ ] **Step 2: Commit**

```bash
git add extractor/reporter.py
git commit -m "feat(#5/T8): reporter — Rich-formatted extraction report with accept/skip"
```

---

### Task 9: Emitter

**Files:**
- Create: `extractor/emitter.py`
- Create: `tests/test_extractor/test_emitter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extractor/test_emitter.py
"""Tests for extractor.emitter — AnalysisReport to contract YAML."""
from pathlib import Path

import pytest
import yaml

from extractor.emitter import emit_contracts
from extractor.models import (
    AnalysisReport,
    ExtractedEntity,
    ExtractedField,
    ExtractedWorkflow,
)


class TestEmitContracts:

    def test_emits_entity_contracts(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(
                    name="product",
                    source_file="models.py",
                    description="A product",
                    fields=[
                        ExtractedField(name="name", type="string", required=True),
                        ExtractedField(name="price", type="number"),
                    ],
                ),
            ],
        )
        files = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        assert len(files) >= 1
        entity_file = tmp_path / "domains" / "shop" / "entities" / "product.contract.yaml"
        assert entity_file.exists()

        contract = yaml.safe_load(entity_file.read_text(encoding="utf-8"))
        assert contract["kind"] == "Entity"
        assert contract["metadata"]["name"] == "product"

    def test_emits_route_and_page(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(name="product", source_file="m.py", fields=[
                    ExtractedField(name="name", type="string"),
                ]),
            ],
        )
        files = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        route_file = tmp_path / "domains" / "shop" / "routes" / "products.contract.yaml"
        assert route_file.exists()

        page_file = tmp_path / "domains" / "shop" / "pages" / "products.contract.yaml"
        assert page_file.exists()

    def test_emits_workflow(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[ExtractedEntity(name="order", source_file="m.py", fields=[])],
            workflows=[
                ExtractedWorkflow(
                    name="order_lifecycle",
                    entity_name="order",
                    states=["pending", "shipped", "delivered"],
                    initial="pending",
                    source_file="m.py",
                ),
            ],
        )
        files = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        wf_file = tmp_path / "domains" / "shop" / "workflows" / "order_lifecycle.contract.yaml"
        assert wf_file.exists()
```

- [ ] **Step 2: Implement emitter**

```python
# extractor/emitter.py
"""Emit contracts from an AnalysisReport using Factory emitters."""
from __future__ import annotations

from pathlib import Path

from factory.emitters.entity_emitter import emit_entity
from factory.emitters.page_emitter import emit_page
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from forge.normalize import normalize_name
from extractor.models import AnalysisReport, ExtractedEntity


def emit_contracts(
    report: AnalysisReport,
    output_dir: Path,
    accepted_entities: list[ExtractedEntity] | None = None,
) -> list[Path]:
    """Emit contract YAML files from an AnalysisReport.

    Uses the Factory emitters (same normalization + validation).
    Returns list of written file paths.
    """
    entities = accepted_entities if accepted_entities is not None else report.entities
    domain = report.domain
    written: list[Path] = []

    # Emit entities
    for entity in entities:
        safe_name = normalize_name(entity.name)
        data = entity.to_emitter_data()

        # Add workflow reference if applicable
        for wf in report.workflows:
            if wf.entity_name == safe_name:
                data["state_machine"] = f"workflow/{domain}/{normalize_name(wf.name)}"
                break

        yaml_str = emit_entity(safe_name, domain, data)
        path = output_dir / "entities" / f"{safe_name}.contract.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_str, encoding="utf-8")
        written.append(path)

        # Emit route
        entity_fqn = f"entity/{domain}/{safe_name}"
        plural = safe_name + "s"
        workflow_fqn = ""
        for wf in report.workflows:
            if wf.entity_name == safe_name:
                workflow_fqn = f"workflow/{domain}/{normalize_name(wf.name)}"
                break

        route_yaml = emit_route(plural, domain, entity_fqn, workflow_fqn)
        route_path = output_dir / "routes" / f"{plural}.contract.yaml"
        route_path.parent.mkdir(parents=True, exist_ok=True)
        route_path.write_text(route_yaml, encoding="utf-8")
        written.append(route_path)

        # Emit page
        field_names = [f.name for f in entity.fields]
        page_yaml = emit_page(plural, domain, entity_fqn, field_names)
        page_path = output_dir / "pages" / f"{plural}.contract.yaml"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(page_yaml, encoding="utf-8")
        written.append(page_path)

    # Emit workflows
    for wf in report.workflows:
        safe_name = normalize_name(wf.name)
        data = wf.to_emitter_data()
        yaml_str = emit_workflow(safe_name, domain, data)
        path = output_dir / "workflows" / f"{safe_name}.contract.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_str, encoding="utf-8")
        written.append(path)

    return written
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_extractor/test_emitter.py -v`

- [ ] **Step 4: Commit**

```bash
git add extractor/emitter.py tests/test_extractor/test_emitter.py
git commit -m "feat(#5/T9): emitter — AnalysisReport to contract YAML via Factory emitters"
```

---

### Task 10: CLI Command + REPL Integration

**Files:**
- Create: `extractor/cli/commands.py`
- Modify: `forge/cli/main.py` — register extract command
- Modify: `forge/cli/repl.py` — add /extract slash command
- Modify: `pyproject.toml` — add extractor to setuptools packages

- [ ] **Step 1: Implement CLI command**

```python
# extractor/cli/commands.py
"""specora extract — reverse-engineer codebases into contracts."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.rule import Rule

from extractor.emitter import emit_contracts
from extractor.reporter import display_report
from extractor.synthesizer import synthesize
from forge.normalize import normalize_name

console = Console()


@click.command("extract")
@click.argument("path", type=click.Path(exists=True))
@click.option("--domain", "-d", default="", help="Domain name (auto-inferred from directory name if omitted)")
@click.option("--output", "-o", default="domains/", help="Output base directory")
def extract(path: str, domain: str, output: str) -> None:
    """Reverse-engineer a codebase into Specora contracts.

    Analyzes Python and TypeScript source files, extracts entities,
    routes, and workflows, then emits contract YAML files.
    """
    source_path = Path(path)

    # Auto-infer domain name
    if not domain:
        domain = normalize_name(source_path.name)
    domain = normalize_name(domain)

    console.print()
    console.print(Rule(f"[bold magenta]Extracting: {source_path}[/bold magenta]", style="magenta"))
    console.print(f"  [dim]Domain: {domain}[/dim]")
    console.print()

    # Run the 4-pass pipeline
    start = time.time()
    with console.status("[magenta]Scanning files…[/magenta]", spinner="dots"):
        report = synthesize(source_path, domain)
    elapsed = time.time() - start

    console.print(f"  [dim]Scanned {report.files_scanned} files, analyzed {report.files_analyzed} ({elapsed:.1f}s)[/dim]")
    console.print()

    if not report.entities:
        console.print("  [yellow]No entities found. The codebase may not contain recognizable models.[/yellow]")
        return

    # Display report and get user approvals
    accepted = display_report(report)

    if not accepted:
        console.print("  [yellow]No entities accepted. No contracts written.[/yellow]")
        return

    # Confirm write
    output_dir = Path(output) / domain
    console.print(f"  Writing {len(accepted)} entities (+ routes + pages) to [cyan]{output_dir}[/cyan]")
    try:
        response = console.input("  [bold]Proceed? [Y/n] [/bold]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n  [dim]Cancelled.[/dim]")
        return

    if response not in ("", "y", "yes"):
        console.print("  [yellow]Cancelled.[/yellow]")
        return

    # Emit
    written = emit_contracts(report, output_dir, accepted_entities=accepted)

    console.print()
    for p in written:
        console.print(f"  [green]✓[/green] {p}")

    console.print()
    console.print(Rule(style="dim"))
    console.print(f"  [bold green]Wrote {len(written)} contracts to {output_dir}[/bold green]")
    console.print()
    console.print("  [dim]Next steps:[/dim]")
    console.print(f"  [dim]  spc forge validate {output_dir}[/dim]")
    console.print(f"  [dim]  spc forge generate {output_dir}[/dim]")
    console.print()
```

- [ ] **Step 2: Register in main CLI**

Add to `forge/cli/main.py` after the healer registration:

```python
# Import and register extractor commands
from extractor.cli.commands import extract as extract_cmd
cli.add_command(extract_cmd, "extract")
```

- [ ] **Step 3: Add /extract to REPL**

In `forge/cli/repl.py`, add to `COMMAND_HELP`:
```python
"/extract <path> [--domain <d>]": "Reverse-engineer codebase into contracts",
```

Add to `COMMANDS` list:
```python
"/extract",
```

Add handler function:
```python
def cmd_extract(args: str) -> None:
    argv = args.split() if args.strip() else []
    if not argv:
        _err("Usage: /extract <path> [--domain <domain>]")
        return
    _tool(f"extract {' '.join(argv)}")
    from extractor.cli.commands import extract
    _invoke_click(extract, argv)
```

Add to `SLASH_MAP`:
```python
"/extract": cmd_extract,
```

Add to `ROUTE_MAP`:
```python
"extract": cmd_extract,
```

- [ ] **Step 4: Add extractor to setuptools packages**

In `pyproject.toml`, update the include line:
```toml
include = ["forge*", "factory*", "engine*", "healer*", "advisor*", "extractor*", "spec*"]
```

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`

- [ ] **Step 6: Verify CLI**

Run: `python -m forge.cli.main extract --help`
Run: `python -m forge.cli.main --help` (should show `extract` command)

- [ ] **Step 7: Commit**

```bash
git add extractor/cli/commands.py forge/cli/main.py forge/cli/repl.py pyproject.toml
git commit -m "feat(#5/T10): CLI command + REPL integration — spc extract <path>"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `spc extract --help` — shows extract command with options
- [ ] `spc --help` — shows extract in command list
- [ ] REPL: `/extract` shows in `/help`
- [ ] `spc forge validate domains/library` — existing domains still valid
- [ ] `spc forge validate domains/todo_list` — existing domains still valid

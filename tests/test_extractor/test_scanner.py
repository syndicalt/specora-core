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

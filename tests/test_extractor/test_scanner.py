"""Tests for extractor.scanner — file discovery and classification."""

from pathlib import Path

import pytest

from extractor.models import FileRole
from extractor.scanner import ScanLimits, scan_directory


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
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/users')\n"
        "def list_users(): pass\n",
        encoding="utf-8",
    )
    # Tests (should be skipped)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_user.py").write_text("def test_user(): pass\n", encoding="utf-8")
    # Config
    (tmp_path / "config.py").write_text("DATABASE_URL = 'postgres://'\n", encoding="utf-8")
    # TypeScript
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "types.ts").write_text(
        "export interface User { name: string; }\n", encoding="utf-8"
    )
    # Migration
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001_init.sql").write_text(
        "CREATE TABLE users (id INT);\n", encoding="utf-8"
    )
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

    def test_classifies_files_under_a_test_directory(self, sample_project: Path) -> None:
        (sample_project / "tests" / "helpers.py").write_text("x = 1\n", encoding="utf-8")
        results = {f.path: f for f in scan_directory(sample_project)}
        assert results["tests/helpers.py"].role == FileRole.TEST


class TestHostileInput:
    """The scan root is a codebase the user did not write. Every file in it is
    untrusted, and so is every path that claims to be in it."""

    def test_symlink_out_of_the_root_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "id_rsa"
        secret.write_text("PRIVATE KEY\n", encoding="utf-8")

        root = tmp_path / "repo"
        root.mkdir()
        (root / "models.py").write_text("class A: pass\n", encoding="utf-8")
        (root / "secrets_model.py").symlink_to(secret)

        warnings: list[str] = []
        results = scan_directory(root, warnings=warnings)

        assert [f.path for f in results] == ["models.py"]
        assert any("escapes the scan root" in w for w in warnings)

    def test_symlinked_directory_is_not_followed(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / "app").mkdir(parents=True)
        (root / "app" / "models.py").write_text("class A: pass\n", encoding="utf-8")
        (root / "loop").symlink_to(root, target_is_directory=True)

        warnings: list[str] = []
        results = scan_directory(root, warnings=warnings)

        assert [f.path for f in results] == ["app/models.py"]
        assert any("symlinked directory" in w for w in warnings)

    def test_oversized_file_is_skipped_and_reported(self, tmp_path: Path) -> None:
        (tmp_path / "models.py").write_text("x = 1\n" * 5000, encoding="utf-8")
        (tmp_path / "small.py").write_text("class A: pass\n", encoding="utf-8")

        warnings: list[str] = []
        results = scan_directory(tmp_path, limits=ScanLimits(max_file_bytes=64), warnings=warnings)

        assert [f.path for f in results] == ["small.py"]
        assert any("exceeds the" in w for w in warnings)

    def test_deep_tree_is_bounded(self, tmp_path: Path) -> None:
        deep = tmp_path
        for i in range(40):
            deep = deep / f"n{i}"
        deep.mkdir(parents=True)
        (deep / "models.py").write_text("class A: pass\n", encoding="utf-8")

        warnings: list[str] = []
        results = scan_directory(tmp_path, limits=ScanLimits(max_depth=5), warnings=warnings)

        assert results == []
        assert any("directory limit" in w for w in warnings)

    def test_file_count_is_bounded(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"m{i}.py").write_text("class A: pass\n", encoding="utf-8")

        warnings: list[str] = []
        results = scan_directory(tmp_path, limits=ScanLimits(max_files=3), warnings=warnings)

        assert len(results) == 3
        assert any("file limit" in w for w in warnings)

    def test_missing_root_raises_rather_than_reporting_an_empty_codebase(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            scan_directory(tmp_path / "nope")

    def test_binary_and_undecodable_files_do_not_crash_classification(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "weird.py").write_bytes(b"\xff\xfe\x00binary garbage\x00")
        results = scan_directory(tmp_path)
        assert [f.path for f in results] == ["weird.py"]

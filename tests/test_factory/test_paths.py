"""Tests for factory.paths — the name and filesystem safety layer.

Every path the Factory writes is built from a name that came from an LLM or a
command line, so these are the checks that keep model output inside
``domains/``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from factory.paths import UnsafeNameError, contract_path, safe_name, write_atomic


class TestSafeName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ticket", "ticket"),
            ("TodoList", "todo_list"),
            ("todoList", "todo_list"),
            ("Task_lifecycle", "task_lifecycle"),
            ("__init__", "init"),
        ],
    )
    def test_accepts_and_normalizes(self, raw: str, expected: str) -> None:
        assert safe_name(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "../../../etc/passwd",
            "..",
            "a/b",
            "  ",
            "",
            "café",
            "1abc",
            "SELECT * FROM x",
            "name\nwith\nnewlines",
            "x" * 300 + "/../y",
            "..%2f..%2fetc",
        ],
    )
    def test_rejects_unusable(self, raw: str) -> None:
        with pytest.raises(UnsafeNameError):
            safe_name(raw)

    @pytest.mark.parametrize("raw", [None, 42, {"name": "x"}, ["x"]])
    def test_rejects_non_strings(self, raw: object) -> None:
        # str(dict) would produce a plausible-looking name from a malformed
        # tool call, so coercion is not an option.
        with pytest.raises(UnsafeNameError):
            safe_name(raw)

    def test_long_legal_name_is_accepted(self) -> None:
        assert safe_name("a" * 300) == "a" * 300


class TestContractPath:
    def test_builds_expected_layout(self, tmp_path: Path) -> None:
        assert contract_path(tmp_path, "shop", "entity", "Product") == (
            tmp_path / "shop" / "entities" / "product.contract.yaml"
        )
        assert contract_path(tmp_path, "shop", "page", "products") == (
            tmp_path / "shop" / "pages" / "products.contract.yaml"
        )

    @pytest.mark.parametrize(
        ("domain", "name"),
        [
            ("..", "x"),
            ("shop", "../../../../tmp/pwn"),
            ("shop", "a/b"),
            ("../secrets", "x"),
        ],
    )
    def test_rejects_traversal(self, tmp_path: Path, domain: str, name: str) -> None:
        with pytest.raises(UnsafeNameError):
            contract_path(tmp_path, domain, "entity", name)

    def test_rejects_unknown_kind(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafeNameError):
            contract_path(tmp_path, "shop", "sprocket", "widget")

    def test_result_always_under_base(self, tmp_path: Path) -> None:
        path = contract_path(tmp_path, "shop", "route", "products")
        assert tmp_path.resolve() in path.resolve().parents


class TestWriteAtomic:
    def test_creates_parents_and_writes(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.yaml"
        write_atomic(target, "hello: world\n")
        assert target.read_text(encoding="utf-8") == "hello: world\n"

    def test_leaves_no_temp_files_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "c.yaml"
        write_atomic(target, "a: 1\n")
        assert [p.name for p in tmp_path.iterdir()] == ["c.yaml"]

    def test_failed_write_leaves_original_intact(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "c.yaml"
        write_atomic(target, "original: true\n")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            write_atomic(target, "replacement: true\n")

        assert target.read_text(encoding="utf-8") == "original: true\n"
        assert [p.name for p in tmp_path.iterdir()] == ["c.yaml"]

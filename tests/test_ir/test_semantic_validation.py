from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forge.ir.compiler import CompilationError, Compiler


def _write_contract(path: Path, contract: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(contract, sort_keys=False), encoding="utf-8")


def _entity(name: str, fields: dict, **spec_extra) -> dict:
    return {
        "apiVersion": "specora.dev/v1",
        "kind": "Entity",
        "metadata": {"name": name, "domain": "test"},
        "requires": spec_extra.pop("requires", []),
        "spec": {"fields": fields, **spec_extra},
    }


def _compile_raises(tmp_path: Path) -> str:
    with pytest.raises(CompilationError) as exc:
        Compiler(contract_root=tmp_path, include_stdlib=False).compile()
    return str(exc.value)


def test_missing_semantic_field_reference_fails_compilation(tmp_path: Path) -> None:
    _write_contract(
        tmp_path / "entities/task.contract.yaml",
        _entity(
            "task",
            {
                "title": {"type": "string"},
                "owner_id": {
                    "type": "string",
                    "references": {"entity": "entity/test/user", "display": "name"},
                },
            },
        ),
    )

    message = _compile_raises(tmp_path)

    assert "requires 'entity/test/user' which does not exist" in message


def test_missing_route_entity_fails_compilation(tmp_path: Path) -> None:
    _write_contract(
        tmp_path / "routes/tasks.contract.yaml",
        {
            "apiVersion": "specora.dev/v1",
            "kind": "Route",
            "metadata": {"name": "tasks", "domain": "test"},
            "requires": [],
            "spec": {
                "entity": "entity/test/task",
                "endpoints": [
                    {"method": "GET", "path": "/", "summary": "List tasks"},
                ],
            },
        },
    )

    message = _compile_raises(tmp_path)

    assert "requires 'entity/test/task' which does not exist" in message


def test_workflow_guard_missing_entity_field_fails_compilation(tmp_path: Path) -> None:
    _write_contract(
        tmp_path / "entities/task.contract.yaml",
        _entity(
            "task",
            {"title": {"type": "string"}},
            requires=["workflow/test/task_lifecycle"],
            state_machine="workflow/test/task_lifecycle",
        ),
    )
    _write_contract(
        tmp_path / "workflows/task_lifecycle.contract.yaml",
        {
            "apiVersion": "specora.dev/v1",
            "kind": "Workflow",
            "metadata": {"name": "task_lifecycle", "domain": "test"},
            "requires": [],
            "spec": {
                "initial": "new",
                "states": {
                    "new": {"label": "New"},
                    "done": {"label": "Done", "terminal": True},
                },
                "transitions": {"new": ["done"]},
                "guards": {"new -> done": {"require_fields": ["resolution"]}},
            },
        },
    )

    message = _compile_raises(tmp_path)

    assert "requires missing field 'resolution'" in message


def test_workflow_transition_to_unknown_state_fails_compilation(tmp_path: Path) -> None:
    _write_contract(
        tmp_path / "workflows/task_lifecycle.contract.yaml",
        {
            "apiVersion": "specora.dev/v1",
            "kind": "Workflow",
            "metadata": {"name": "task_lifecycle", "domain": "test"},
            "requires": [],
            "spec": {
                "initial": "new",
                "states": {"new": {"label": "New"}},
                "transitions": {"new": ["done"]},
            },
        },
    )

    message = _compile_raises(tmp_path)

    assert "transition target 'done' is not declared" in message

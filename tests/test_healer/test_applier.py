"""Tests for healer.applier — apply fixes with rollback."""
import yaml
from pathlib import Path

import pytest

from healer.applier import apply_fix, ApplyResult
from healer.models import HealerProposal


@pytest.fixture
def domain_dir(tmp_path: Path) -> Path:
    d = tmp_path / "domains" / "test" / "entities"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def contract_path(domain_dir: Path) -> Path:
    p = domain_dir / "task.contract.yaml"
    contract = {
        "apiVersion": "specora.dev/v1",
        "kind": "Entity",
        "metadata": {"name": "Task", "domain": "test"},
        "requires": [],
        "spec": {"fields": {"name": {"type": "string", "required": True}}},
    }
    p.write_text(yaml.dump(contract), encoding="utf-8")
    return p


class TestApplyFix:

    def test_applies_valid_fix(self, contract_path: Path, tmp_path: Path) -> None:
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={"metadata": {"name": "Task"}},
            after={
                "apiVersion": "specora.dev/v1",
                "kind": "Entity",
                "metadata": {"name": "task", "domain": "test"},
                "requires": [],
                "spec": {"fields": {"name": {"type": "string", "required": True}}},
            },
            changes=[],
            explanation="Fixed name",
            confidence=1.0,
            method="deterministic",
        )
        result = apply_fix(proposal, contract_path, diff_root=tmp_path / ".forge" / "diffs")
        assert result.success is True
        content = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert content["metadata"]["name"] == "task"

    def test_rollback_on_invalid_fix(self, contract_path: Path, tmp_path: Path) -> None:
        original = contract_path.read_text(encoding="utf-8")
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={},
            after={"apiVersion": "wrong", "kind": "Entity", "metadata": {"name": "task", "domain": "test"}, "requires": [], "spec": {"fields": {}}},
            changes=[],
            explanation="Bad fix",
            confidence=0.5,
            method="llm_structural",
        )
        result = apply_fix(proposal, contract_path, diff_root=tmp_path / ".forge" / "diffs")
        assert result.success is False
        assert contract_path.read_text(encoding="utf-8") == original

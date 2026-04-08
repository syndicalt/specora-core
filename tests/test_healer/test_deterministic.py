"""Tests for healer.proposer.deterministic — Tier 1 normalize fixes."""
import copy

import pytest

from healer.proposer.deterministic import propose_deterministic_fix


class TestDeterministicFix:

    @pytest.fixture
    def broken_contract(self) -> dict:
        return {
            "apiVersion": "specora.dev/v1",
            "kind": "Entity",
            "metadata": {"name": "Task", "domain": "todo_list"},
            "requires": ["mixin/stdlib/timestamped", "todo_list/User"],
            "spec": {
                "fields": {
                    "assigned_to": {
                        "type": "uuid",
                        "references": {
                            "entity": "todo_list/User",
                            "graph_edge": "assigned_to",
                        },
                    },
                },
            },
        }

    def test_proposes_fix(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        assert proposal.confidence == 1.0
        assert proposal.method == "deterministic"

    def test_fixes_metadata_name(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        assert proposal.after["metadata"]["name"] == "task"

    def test_fixes_requires_fqn(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        assert "entity/todo_list/user" in proposal.after["requires"]

    def test_fixes_graph_edge(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        edge = proposal.after["spec"]["fields"]["assigned_to"]["references"]["graph_edge"]
        assert edge == "ASSIGNED_TO"

    def test_returns_none_if_already_valid(self) -> None:
        valid = {
            "apiVersion": "specora.dev/v1",
            "kind": "Entity",
            "metadata": {"name": "task", "domain": "todo_list"},
            "requires": ["mixin/stdlib/timestamped"],
            "spec": {"fields": {"name": {"type": "string"}}},
        }
        proposal = propose_deterministic_fix("entity/todo_list/task", valid)
        assert proposal is None

    def test_has_changes_list(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        assert len(proposal.changes) > 0

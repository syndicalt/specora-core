"""Tests for forge.normalize — deterministic contract normalization."""
import copy

import pytest

from forge.normalize import (
    normalize_contract,
    normalize_fqn,
    normalize_graph_edge,
    normalize_name,
)


# ── normalize_name ──────────────────────────────────────────────────────


class TestNormalizeName:
    """Names must match ^[a-z][a-z0-9_]*$."""

    @pytest.mark.parametrize(
        "input_name, expected",
        [
            ("task", "task"),
            ("Task", "task"),
            ("TodoList", "todo_list"),
            ("todoList", "todo_list"),
            ("Task_lifecycle", "task_lifecycle"),
            ("User_lifecycle", "user_lifecycle"),
            ("HTTPServer", "http_server"),
            ("getHTTPResponse", "get_http_response"),
            ("already_snake", "already_snake"),
            ("Tag", "tag"),
            ("ID", "id"),
        ],
    )
    def test_cases(self, input_name: str, expected: str) -> None:
        assert normalize_name(input_name) == expected

    def test_result_matches_pattern(self) -> None:
        import re

        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        names = ["Task", "TodoList", "User_lifecycle", "HTTPServer", "getHTTPResponse"]
        for name in names:
            result = normalize_name(name)
            assert pattern.match(result), f"{name!r} → {result!r} doesn't match"


# ── normalize_fqn ───────────────────────────────────────────────────────


class TestNormalizeFqn:
    """FQNs must match ^(entity|workflow|...)/[a-z][a-z0-9_/]*$."""

    def test_short_form_domain_slash_name(self) -> None:
        assert normalize_fqn("todo_list/User", "entity", "todo_list") == "entity/todo_list/user"

    def test_already_fqn_but_wrong_case(self) -> None:
        assert normalize_fqn("entity/todo_list/User", "entity", "todo_list") == "entity/todo_list/user"

    def test_workflow_fqn_with_mixed_case(self) -> None:
        assert (
            normalize_fqn("workflow/todo_list/Task_lifecycle", "workflow", "todo_list")
            == "workflow/todo_list/task_lifecycle"
        )

    def test_bare_name(self) -> None:
        assert normalize_fqn("User", "entity", "todo_list") == "entity/todo_list/user"

    def test_mixin_fqn_preserved(self) -> None:
        assert normalize_fqn("mixin/stdlib/timestamped", "mixin", "todo_list") == "mixin/stdlib/timestamped"

    def test_result_matches_requires_pattern(self) -> None:
        import re

        pattern = re.compile(r"^(entity|workflow|page|route|agent|mixin|infra)/[a-z][a-z0-9_/]*$")
        cases = [
            ("todo_list/User", "entity", "todo_list"),
            ("workflow/todo_list/Task_lifecycle", "workflow", "todo_list"),
            ("entity/todo_list/User", "entity", "todo_list"),
            ("mixin/stdlib/timestamped", "mixin", "todo_list"),
            ("User", "entity", "todo_list"),
        ]
        for ref, kind, domain in cases:
            result = normalize_fqn(ref, kind, domain)
            assert pattern.match(result), f"{ref!r} → {result!r} doesn't match"


# ── normalize_graph_edge ────────────────────────────────────────────────


class TestNormalizeGraphEdge:
    """Graph edges must match ^[A-Z][A-Z0-9_]*$."""

    @pytest.mark.parametrize(
        "input_edge, expected",
        [
            ("assigned_to", "ASSIGNED_TO"),
            ("ASSIGNED_TO", "ASSIGNED_TO"),
            ("AssignedTo", "ASSIGNED_TO"),
            ("reportedBy", "REPORTED_BY"),
            ("CONTAINS", "CONTAINS"),
        ],
    )
    def test_cases(self, input_edge: str, expected: str) -> None:
        assert normalize_graph_edge(input_edge) == expected

    def test_result_matches_pattern(self) -> None:
        import re

        pattern = re.compile(r"^[A-Z][A-Z0-9_]*$")
        edges = ["assigned_to", "AssignedTo", "reportedBy"]
        for edge in edges:
            result = normalize_graph_edge(edge)
            assert pattern.match(result), f"{edge!r} → {result!r} doesn't match"


# ── normalize_contract ──────────────────────────────────────────────────


class TestNormalizeContract:
    """Full contract normalization — the main entry point."""

    @pytest.fixture
    def broken_entity(self) -> dict:
        """A contract as the Factory LLM would produce it — all the bugs."""
        return {
            "apiVersion": "specora.dev/v1",
            "kind": "Entity",
            "metadata": {
                "name": "Task",
                "domain": "todo_list",
                "description": "A task entity",
            },
            "requires": [
                "mixin/stdlib/timestamped",
                "mixin/stdlib/identifiable",
                "todo_list/User",
                "workflow/todo_list/Task_lifecycle",
            ],
            "spec": {
                "fields": {
                    "title": {"type": "string", "required": True},
                    "assigned_to": {
                        "type": "uuid",
                        "references": {
                            "entity": "todo_list/User",
                            "display": "name",
                            "graph_edge": "assigned_to",
                        },
                    },
                },
                "mixins": [
                    "mixin/stdlib/timestamped",
                    "mixin/stdlib/identifiable",
                ],
                "state_machine": "workflow/todo_list/Task_lifecycle",
            },
        }

    def test_normalizes_metadata_name(self, broken_entity: dict) -> None:
        result = normalize_contract(broken_entity)
        assert result["metadata"]["name"] == "task"

    def test_normalizes_requires(self, broken_entity: dict) -> None:
        result = normalize_contract(broken_entity)
        assert result["requires"] == [
            "mixin/stdlib/timestamped",
            "mixin/stdlib/identifiable",
            "entity/todo_list/user",
            "workflow/todo_list/task_lifecycle",
        ]

    def test_normalizes_reference_entity(self, broken_entity: dict) -> None:
        result = normalize_contract(broken_entity)
        ref = result["spec"]["fields"]["assigned_to"]["references"]
        assert ref["entity"] == "entity/todo_list/user"

    def test_normalizes_graph_edge(self, broken_entity: dict) -> None:
        result = normalize_contract(broken_entity)
        ref = result["spec"]["fields"]["assigned_to"]["references"]
        assert ref["graph_edge"] == "ASSIGNED_TO"

    def test_normalizes_state_machine(self, broken_entity: dict) -> None:
        result = normalize_contract(broken_entity)
        assert result["spec"]["state_machine"] == "workflow/todo_list/task_lifecycle"

    def test_normalizes_mixins(self, broken_entity: dict) -> None:
        result = normalize_contract(broken_entity)
        assert result["spec"]["mixins"] == [
            "mixin/stdlib/timestamped",
            "mixin/stdlib/identifiable",
        ]

    def test_idempotent(self, broken_entity: dict) -> None:
        """Normalizing twice produces the same result."""
        first = normalize_contract(copy.deepcopy(broken_entity))
        second = normalize_contract(copy.deepcopy(first))
        assert first == second

    def test_route_contract(self) -> None:
        contract = {
            "apiVersion": "specora.dev/v1",
            "kind": "Route",
            "metadata": {"name": "Tasks", "domain": "todo_list"},
            "requires": [
                "entity/todo_list/Task",
                "workflow/todo_list/Task_lifecycle",
            ],
            "spec": {
                "entity": "entity/todo_list/Task",
                "base_path": "/tasks",
                "endpoints": [],
            },
        }
        result = normalize_contract(contract)
        assert result["metadata"]["name"] == "tasks"
        assert result["spec"]["entity"] == "entity/todo_list/task"
        assert result["requires"][0] == "entity/todo_list/task"
        assert result["requires"][1] == "workflow/todo_list/task_lifecycle"

    def test_page_contract(self) -> None:
        contract = {
            "apiVersion": "specora.dev/v1",
            "kind": "Page",
            "metadata": {"name": "TodoLists", "domain": "todo_list"},
            "requires": ["entity/todo_list/TodoList"],
            "spec": {
                "route": "/todo_lists",
                "entity": "entity/todo_list/TodoList",
                "generation_tier": "mechanical",
            },
        }
        result = normalize_contract(contract)
        assert result["metadata"]["name"] == "todo_lists"
        assert result["spec"]["entity"] == "entity/todo_list/todo_list"

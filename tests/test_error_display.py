"""Tests for forge.error_display — human-readable validation errors."""
import pytest

from forge.error_display import FormattedError, humanize_error
from forge.parser.validator import ContractValidationError


class TestHumanizeError:

    def test_snake_case_name_error(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/task",
            path="metadata.name",
            message="'Task' does not match '^[a-z][a-z0-9_]*$'",
        )
        fe = humanize_error(err)
        assert "snake_case" in fe.message
        assert fe.suggestion == "Use 'task' instead of 'Task'"

    def test_fqn_error(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/task",
            path="requires.[2]",
            message="'todo_list/User' does not match '^(entity|workflow|page|route|agent|mixin|infra)/[a-z][a-z0-9_/]*$'",
        )
        fe = humanize_error(err)
        assert "fully qualified name" in fe.message
        assert "entity/todo_list/user" in fe.suggestion

    def test_graph_edge_error(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/task",
            path="spec.fields.assigned_to.references.graph_edge",
            message="'assigned_to' does not match '^[A-Z][A-Z0-9_]*$'",
        )
        fe = humanize_error(err)
        assert "SCREAMING_SNAKE_CASE" in fe.message
        assert fe.suggestion == "Use 'ASSIGNED_TO' instead of 'assigned_to'"

    def test_pascal_case_name_gets_fixed(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/?",
            path="metadata.name",
            message="'TodoList' does not match '^[a-z][a-z0-9_]*$'",
        )
        fe = humanize_error(err)
        assert fe.suggestion == "Use 'todo_list' instead of 'TodoList'"

    def test_unknown_error_passes_through(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/library/book",
            path="spec.fields.title",
            message="Additional properties are not allowed ('foo' was unexpected)",
        )
        fe = humanize_error(err)
        # Should pass through unchanged
        assert "Additional properties" in fe.message
        assert fe.suggestion == ""

    def test_workflow_fqn_in_state_machine_path(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/task",
            path="requires.[3]",
            message="'workflow/todo_list/Task_lifecycle' does not match '^(entity|workflow|page|route|agent|mixin|infra)/[a-z][a-z0-9_/]*$'",
        )
        fe = humanize_error(err)
        assert "workflow/todo_list/task_lifecycle" in fe.suggestion

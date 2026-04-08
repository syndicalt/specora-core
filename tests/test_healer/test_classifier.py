"""Tests for healer.analyzer.classifier — error classification."""
import pytest

from forge.parser.validator import ContractValidationError
from healer.analyzer.classifier import classify_validation_error, classify_raw_error
from healer.models import Priority


class TestClassifyValidationError:

    def test_naming_error(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/?",
            path="metadata.name",
            message="'Task' does not match '^[a-z][a-z0-9_]*$'",
        )
        result = classify_validation_error(err)
        assert result.error_type == "naming"
        assert result.tier == 1
        assert result.priority == Priority.HIGH

    def test_fqn_error(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/task",
            path="requires.[2]",
            message="'todo_list/User' does not match '^(entity|workflow|page|route|agent|mixin|infra)/[a-z][a-z0-9_/]*$'",
        )
        result = classify_validation_error(err)
        assert result.error_type == "fqn_format"
        assert result.tier == 1

    def test_graph_edge_error(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/task",
            path="spec.fields.assigned_to.references.graph_edge",
            message="'assigned_to' does not match '^[A-Z][A-Z0-9_]*$'",
        )
        result = classify_validation_error(err)
        assert result.error_type == "graph_edge"
        assert result.tier == 1

    def test_structural_error(self) -> None:
        err = ContractValidationError(
            contract_fqn="entity/todo_list/task",
            path="spec.fields.title",
            message="Additional properties are not allowed ('foo' was unexpected)",
        )
        result = classify_validation_error(err)
        assert result.error_type == "structural"
        assert result.tier == 2


class TestClassifyRawError:

    def test_runtime_500(self) -> None:
        result = classify_raw_error(
            source="runtime",
            error="Internal Server Error",
            context={"status_code": 500},
        )
        assert result.error_type == "runtime_500"
        assert result.tier == 3
        assert result.priority == Priority.CRITICAL

    def test_compilation_error(self) -> None:
        result = classify_raw_error(
            source="compilation",
            error="CompilationError: unresolved reference entity/lib/nonexistent",
            context={},
        )
        assert result.error_type == "missing_reference"
        assert result.tier == 2

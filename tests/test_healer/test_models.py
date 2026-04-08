"""Tests for healer.models — data structures for the healing pipeline."""
import json
from datetime import datetime, timezone

import pytest

from healer.models import (
    HealerProposal,
    HealerTicket,
    Priority,
    TicketSource,
    TicketStatus,
)


class TestHealerTicket:

    def test_create_minimal_ticket(self) -> None:
        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error="'Task' does not match '^[a-z][a-z0-9_]*$'",
        )
        assert ticket.id  # UUID auto-generated
        assert ticket.status == TicketStatus.QUEUED
        assert ticket.priority == Priority.MEDIUM
        assert ticket.tier == 0
        assert ticket.contract_fqn is None
        assert ticket.proposal is None

    def test_create_full_ticket(self) -> None:
        ticket = HealerTicket(
            source=TicketSource.RUNTIME,
            contract_fqn="entity/todo_list/task",
            error_type="runtime_500",
            raw_error="TypeError: 'NoneType' object is not subscriptable",
            context={"request_path": "/api/tasks/123", "method": "PATCH"},
            tier=3,
            priority=Priority.CRITICAL,
        )
        assert ticket.contract_fqn == "entity/todo_list/task"
        assert ticket.tier == 3
        assert ticket.priority == Priority.CRITICAL

    def test_ticket_serializes_to_dict(self) -> None:
        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error="test error",
        )
        d = ticket.to_dict()
        assert d["source"] == "validation"
        assert d["status"] == "queued"
        json.dumps(d)

    def test_ticket_round_trips(self) -> None:
        ticket = HealerTicket(
            source=TicketSource.COMPILATION,
            raw_error="CompilationError: cycle detected",
            contract_fqn="entity/lib/book",
            tier=2,
            priority=Priority.HIGH,
        )
        d = ticket.to_dict()
        restored = HealerTicket.from_dict(d)
        assert restored.id == ticket.id
        assert restored.source == ticket.source
        assert restored.priority == ticket.priority


class TestHealerProposal:

    def test_create_proposal(self) -> None:
        proposal = HealerProposal(
            contract_fqn="entity/todo_list/task",
            before={"metadata": {"name": "Task"}},
            after={"metadata": {"name": "task"}},
            changes=[],
            explanation="Normalized name to snake_case",
            confidence=1.0,
            method="deterministic",
        )
        assert proposal.confidence == 1.0
        assert proposal.method == "deterministic"

    def test_proposal_serializes(self) -> None:
        proposal = HealerProposal(
            contract_fqn="entity/todo_list/task",
            before={},
            after={},
            changes=[],
            explanation="test",
            confidence=0.8,
            method="llm_structural",
        )
        d = proposal.to_dict()
        json.dumps(d)
        restored = HealerProposal.from_dict(d)
        assert restored.confidence == 0.8


class TestEnums:

    def test_ticket_source_values(self) -> None:
        assert TicketSource.VALIDATION.value == "validation"
        assert TicketSource.COMPILATION.value == "compilation"
        assert TicketSource.RUNTIME.value == "runtime"
        assert TicketSource.MANUAL.value == "manual"

    def test_priority_ordering(self) -> None:
        ordered = [Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM, Priority.LOW]
        assert [p.value for p in ordered] == ["critical", "high", "medium", "low"]

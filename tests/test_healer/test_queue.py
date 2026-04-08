# tests/test_healer/test_queue.py
"""Tests for healer.queue — SQLite-backed priority queue."""
import tempfile
from pathlib import Path

import pytest

from healer.models import HealerTicket, Priority, TicketSource, TicketStatus
from healer.queue import HealerQueue


@pytest.fixture
def queue(tmp_path: Path) -> HealerQueue:
    return HealerQueue(db_path=tmp_path / "healer.db")


class TestEnqueueAndGet:

    def test_enqueue_returns_id(self, queue: HealerQueue) -> None:
        ticket = HealerTicket(source=TicketSource.VALIDATION, raw_error="test")
        ticket_id = queue.enqueue(ticket)
        assert ticket_id == ticket.id

    def test_get_ticket(self, queue: HealerQueue) -> None:
        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error="test",
            contract_fqn="entity/lib/book",
        )
        queue.enqueue(ticket)
        retrieved = queue.get_ticket(ticket.id)
        assert retrieved is not None
        assert retrieved.contract_fqn == "entity/lib/book"

    def test_get_nonexistent_returns_none(self, queue: HealerQueue) -> None:
        assert queue.get_ticket("nonexistent") is None


class TestNextQueued:

    def test_returns_highest_priority(self, queue: HealerQueue) -> None:
        low = HealerTicket(source=TicketSource.VALIDATION, raw_error="low", priority=Priority.LOW)
        critical = HealerTicket(source=TicketSource.RUNTIME, raw_error="critical", priority=Priority.CRITICAL)
        queue.enqueue(low)
        queue.enqueue(critical)

        nxt = queue.next_queued()
        assert nxt is not None
        assert nxt.id == critical.id

    def test_returns_none_when_empty(self, queue: HealerQueue) -> None:
        assert queue.next_queued() is None

    def test_fifo_within_same_priority(self, queue: HealerQueue) -> None:
        first = HealerTicket(source=TicketSource.VALIDATION, raw_error="first", priority=Priority.HIGH)
        second = HealerTicket(source=TicketSource.VALIDATION, raw_error="second", priority=Priority.HIGH)
        queue.enqueue(first)
        queue.enqueue(second)

        nxt = queue.next_queued()
        assert nxt is not None
        assert nxt.id == first.id


class TestUpdateStatus:

    def test_update_status(self, queue: HealerQueue) -> None:
        ticket = HealerTicket(source=TicketSource.VALIDATION, raw_error="test")
        queue.enqueue(ticket)
        queue.update_status(ticket.id, TicketStatus.ANALYZING)

        retrieved = queue.get_ticket(ticket.id)
        assert retrieved is not None
        assert retrieved.status == TicketStatus.ANALYZING

    def test_update_with_resolution_note(self, queue: HealerQueue) -> None:
        ticket = HealerTicket(source=TicketSource.VALIDATION, raw_error="test")
        queue.enqueue(ticket)
        queue.update_status(ticket.id, TicketStatus.APPLIED, resolution_note="Fixed via normalize")

        retrieved = queue.get_ticket(ticket.id)
        assert retrieved is not None
        assert retrieved.resolution_note == "Fixed via normalize"


class TestListAndStats:

    def test_list_tickets_by_status(self, queue: HealerQueue) -> None:
        t1 = HealerTicket(source=TicketSource.VALIDATION, raw_error="a")
        t2 = HealerTicket(source=TicketSource.VALIDATION, raw_error="b")
        queue.enqueue(t1)
        queue.enqueue(t2)
        queue.update_status(t2.id, TicketStatus.APPLIED)

        queued = queue.list_tickets(status=TicketStatus.QUEUED)
        assert len(queued) == 1
        assert queued[0].id == t1.id

    def test_stats(self, queue: HealerQueue) -> None:
        t1 = HealerTicket(source=TicketSource.VALIDATION, raw_error="a", priority=Priority.HIGH)
        t2 = HealerTicket(source=TicketSource.RUNTIME, raw_error="b", priority=Priority.CRITICAL)
        queue.enqueue(t1)
        queue.enqueue(t2)
        queue.update_status(t2.id, TicketStatus.APPLIED)

        stats = queue.stats()
        assert stats["by_status"]["queued"] == 1
        assert stats["by_status"]["applied"] == 1
        assert stats["total"] == 2

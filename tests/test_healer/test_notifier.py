"""Tests for healer.notifier — notification channels."""
import json
from pathlib import Path

import pytest

from healer.models import HealerTicket, TicketSource, TicketStatus, Priority
from healer.notifier import Notifier


@pytest.fixture
def notifier(tmp_path: Path) -> Notifier:
    return Notifier(log_path=tmp_path / "notifications.jsonl")


class TestFileNotification:

    def test_logs_to_jsonl(self, notifier: Notifier) -> None:
        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error="test error",
            contract_fqn="entity/test/task",
        )
        notifier.notify(ticket, event="queued")

        lines = notifier.log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "queued"
        assert entry["contract_fqn"] == "entity/test/task"

    def test_appends_multiple(self, notifier: Notifier) -> None:
        t1 = HealerTicket(source=TicketSource.VALIDATION, raw_error="a")
        t2 = HealerTicket(source=TicketSource.RUNTIME, raw_error="b")
        notifier.notify(t1, event="queued")
        notifier.notify(t2, event="applied")

        lines = notifier.log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

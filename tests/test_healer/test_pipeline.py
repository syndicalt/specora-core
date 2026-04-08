"""Tests for healer.pipeline — end-to-end pipeline orchestration."""
import yaml
from pathlib import Path

import pytest

from healer.models import HealerTicket, TicketSource, TicketStatus
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue


@pytest.fixture
def setup(tmp_path: Path):
    queue = HealerQueue(db_path=tmp_path / "healer.db")

    domain_dir = tmp_path / "domains" / "todo_list" / "entities"
    domain_dir.mkdir(parents=True)
    broken = {
        "apiVersion": "specora.dev/v1",
        "kind": "Entity",
        "metadata": {"name": "Task", "domain": "todo_list"},
        "requires": ["mixin/stdlib/timestamped"],
        "spec": {"fields": {"name": {"type": "string", "required": True}}},
    }
    contract_path = domain_dir / "task.contract.yaml"
    contract_path.write_text(yaml.dump(broken), encoding="utf-8")

    pipeline = HealerPipeline(
        queue=queue,
        domains_root=tmp_path / "domains",
        diff_root=tmp_path / ".forge" / "diffs",
        log_path=tmp_path / "notifications.jsonl",
    )
    return queue, pipeline, contract_path


class TestTier1Pipeline:

    def test_processes_validation_error_end_to_end(self, setup) -> None:
        queue, pipeline, contract_path = setup

        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error="'Task' does not match '^[a-z][a-z0-9_]*$'",
            contract_fqn="entity/todo_list/task",
            context={"source_path": str(contract_path)},
        )
        queue.enqueue(ticket)

        processed = pipeline.process_next()
        assert processed is True

        result = queue.get_ticket(ticket.id)
        assert result is not None
        assert result.status == TicketStatus.APPLIED

        content = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert content["metadata"]["name"] == "task"

    def test_returns_false_when_empty(self, setup) -> None:
        _, pipeline, _ = setup
        assert pipeline.process_next() is False

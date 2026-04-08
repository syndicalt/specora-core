"""Tests for healer.api.server — HTTP endpoint contracts."""
import pytest
from fastapi.testclient import TestClient

from healer.api.server import app
import healer.api.server as srv
from healer.queue import HealerQueue
from healer.pipeline import HealerPipeline


@pytest.fixture(autouse=True)
def reset_globals(tmp_path):
    q = HealerQueue(db_path=tmp_path / "healer.db")
    p = HealerPipeline(
        queue=q,
        domains_root=tmp_path / "domains",
        diff_root=tmp_path / ".forge" / "diffs",
        log_path=tmp_path / "notifications.jsonl",
    )
    srv._queue = q
    srv._pipeline = p
    yield
    srv._queue = None
    srv._pipeline = None


@pytest.fixture
def client():
    return TestClient(app)


class TestIngest:
    def test_ingest_returns_ticket_id(self, client) -> None:
        resp = client.post("/healer/ingest", json={"source": "manual", "error": "test error"})
        assert resp.status_code == 200
        data = resp.json()
        assert "ticket_id" in data

    def test_ingest_with_contract_fqn(self, client) -> None:
        resp = client.post("/healer/ingest", json={
            "source": "validation",
            "contract_fqn": "entity/test/task",
            "error": "'Task' does not match",
        })
        assert resp.status_code == 200


class TestEndpoints:
    def test_health(self, client) -> None:
        assert client.get("/healer/health").json()["status"] == "ok"

    def test_status_empty(self, client) -> None:
        resp = client.get("/healer/status")
        assert resp.status_code == 200

    def test_tickets_empty(self, client) -> None:
        assert client.get("/healer/tickets").json() == []

    def test_ticket_not_found(self, client) -> None:
        assert client.get("/healer/tickets/nonexistent").status_code == 404

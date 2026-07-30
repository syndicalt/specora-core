"""Tests for healer.api.server — HTTP endpoint contracts."""
import pytest
from fastapi.testclient import TestClient

from healer.api.server import app
import healer.api.server as srv
from healer.queue import HealerQueue
from healer.pipeline import HealerPipeline
from healer.ratelimit import TokenBucketLimiter
from healer.security import (
    APPROVAL_SECRET_ENV,
    INGEST_TOKEN_ENV,
    OPERATOR_TOKEN_ENV,
)

INGEST_TOKEN = "test-ingest-token"
OPERATOR_TOKEN = "test-operator-token"
APPROVAL_SECRET = "test-approval-secret"

# Both planes are authenticated now, so every call below carries a credential.
DATA_PLANE_AUTH = {"Authorization": f"Bearer {INGEST_TOKEN}"}
CONTROL_PLANE_AUTH = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}


@pytest.fixture(autouse=True)
def reset_globals(tmp_path, monkeypatch):
    monkeypatch.setenv(INGEST_TOKEN_ENV, INGEST_TOKEN)
    monkeypatch.setenv(OPERATOR_TOKEN_ENV, OPERATOR_TOKEN)
    monkeypatch.setenv(APPROVAL_SECRET_ENV, APPROVAL_SECRET)

    q = HealerQueue(db_path=tmp_path / "healer.db")
    p = HealerPipeline(
        queue=q,
        domains_root=tmp_path / "domains",
        diff_root=tmp_path / ".forge" / "diffs",
        log_path=tmp_path / "notifications.jsonl",
    )
    srv._queue = q
    srv._pipeline = p
    srv._limiter = TokenBucketLimiter()
    yield
    srv._queue = None
    srv._pipeline = None
    srv._limiter = None


@pytest.fixture
def client():
    return TestClient(app)


class TestIngest:
    def test_ingest_returns_ticket_id(self, client) -> None:
        resp = client.post(
            "/healer/ingest",
            json={"source": "manual", "error": "test error"},
            headers=DATA_PLANE_AUTH,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "ticket_id" in data

    def test_ingest_with_contract_fqn(self, client) -> None:
        resp = client.post("/healer/ingest", json={
            "source": "validation",
            "contract_fqn": "entity/test/task",
            "error": "'Task' does not match",
        }, headers=DATA_PLANE_AUTH)
        assert resp.status_code == 202


class TestEndpoints:
    def test_health(self, client) -> None:
        assert client.get("/healer/health").json()["status"] == "ok"

    def test_status_empty(self, client) -> None:
        resp = client.get("/healer/status", headers=DATA_PLANE_AUTH)
        assert resp.status_code == 200

    def test_tickets_empty(self, client) -> None:
        assert client.get("/healer/tickets", headers=CONTROL_PLANE_AUTH).json() == []

    def test_ticket_not_found(self, client) -> None:
        resp = client.get("/healer/tickets/nonexistent", headers=CONTROL_PLANE_AUTH)
        assert resp.status_code == 404

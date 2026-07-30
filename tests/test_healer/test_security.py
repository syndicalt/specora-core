"""Security tests for the Healer HTTP surface and the applier.

Every case here corresponds to a defect that shipped: an unauthenticated
endpoint that rewrites contracts, a stored-XSS sink on the approver's page, a
write-before-validate applier, and a synchronous pipeline call inside an async
handler.
"""
from __future__ import annotations

import time

import pytest
import yaml
from fastapi.testclient import TestClient

import healer.api.server as srv
from healer.api.server import app
from healer.applier import apply_fix, strip_internal_keys
from healer.models import (
    HealerProposal,
    HealerTicket,
    TicketSource,
    TicketStatus,
)
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue
from healer.ratelimit import TokenBucketLimiter
from healer.security import (
    ACTION_APPROVE,
    ACTION_VIEW,
    APPROVAL_SECRET_ENV,
    INGEST_TOKEN_ENV,
    OPERATOR_TOKEN_ENV,
    PROXY_IDENTITY_HEADER_ENV,
    AuthError,
    issue_action_token,
    verify_action_token,
)

INGEST_TOKEN = "ingest-secret"
OPERATOR_TOKEN = "operator-secret"
APPROVAL_SECRET = "approval-secret"

DATA_PLANE_AUTH = {"Authorization": f"Bearer {INGEST_TOKEN}"}
CONTROL_PLANE_AUTH = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}

VALID_CONTRACT = {
    "apiVersion": "specora.dev/v1",
    "kind": "Entity",
    "metadata": {"name": "task", "domain": "test"},
    "requires": [],
    "spec": {"fields": {"name": {"type": "string", "required": True}}},
}


@pytest.fixture(autouse=True)
def healer_env(tmp_path, monkeypatch):
    monkeypatch.setenv(INGEST_TOKEN_ENV, INGEST_TOKEN)
    monkeypatch.setenv(OPERATOR_TOKEN_ENV, OPERATOR_TOKEN)
    monkeypatch.setenv(APPROVAL_SECRET_ENV, APPROVAL_SECRET)
    monkeypatch.delenv(PROXY_IDENTITY_HEADER_ENV, raising=False)

    queue = HealerQueue(db_path=tmp_path / "healer.db")
    pipeline = HealerPipeline(
        queue=queue,
        domains_root=tmp_path / "domains",
        diff_root=tmp_path / ".forge" / "diffs",
        log_path=tmp_path / "notifications.jsonl",
    )
    srv._queue = queue
    srv._pipeline = pipeline
    srv._limiter = TokenBucketLimiter()
    yield queue
    srv._queue = None
    srv._pipeline = None
    srv._limiter = None


@pytest.fixture
def client():
    return TestClient(app)


def _proposed_ticket(queue: HealerQueue, contract_fqn: str = "entity/test/task") -> str:
    ticket = HealerTicket(
        source=TicketSource.VALIDATION,
        raw_error="boom",
        contract_fqn=contract_fqn,
        status=TicketStatus.PROPOSED,
    )
    queue.enqueue(ticket)
    queue.update_status(ticket.id, TicketStatus.PROPOSED)
    queue.set_proposal(
        ticket.id,
        HealerProposal(
            contract_fqn=contract_fqn,
            before={"metadata": {"name": "Task"}},
            after=VALID_CONTRACT,
            changes=[],
            explanation="normalize name",
            confidence=1.0,
            method="deterministic",
        ),
    )
    return ticket.id


# ---------------------------------------------------------------------------
# Control plane authentication
# ---------------------------------------------------------------------------

class TestControlPlaneAuth:

    def test_anonymous_approve_is_rejected(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        resp = client.post(f"/healer/approve/{ticket_id}")
        assert resp.status_code == 401
        assert healer_env.get_ticket(ticket_id).status == TicketStatus.PROPOSED

    def test_anonymous_reject_is_rejected(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        assert client.post(f"/healer/reject/{ticket_id}").status_code == 401

    def test_anonymous_view_is_rejected(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        assert client.get(f"/healer/tickets/{ticket_id}/view").status_code == 401

    def test_anonymous_list_is_rejected(self, client) -> None:
        assert client.get("/healer/tickets").status_code == 401

    def test_every_control_plane_route_denies_anonymous(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        cases = [
            ("GET", f"/healer/tickets/{ticket_id}"),
            ("GET", f"/healer/tickets/{ticket_id}/view"),
            ("GET", "/healer/tickets"),
            ("POST", f"/healer/approve/{ticket_id}"),
            ("POST", f"/healer/reject/{ticket_id}"),
            ("POST", f"/healer/approve/{ticket_id}/action"),
            ("POST", f"/healer/reject/{ticket_id}/action"),
        ]
        for method, path in cases:
            resp = client.request(method, path)
            assert resp.status_code in (401, 403), f"{method} {path} -> {resp.status_code}"

    def test_control_plane_fails_closed_without_configuration(
        self, client, healer_env, monkeypatch
    ) -> None:
        monkeypatch.delenv(OPERATOR_TOKEN_ENV, raising=False)
        monkeypatch.delenv(APPROVAL_SECRET_ENV, raising=False)
        ticket_id = _proposed_ticket(healer_env)
        resp = client.post(f"/healer/approve/{ticket_id}")
        assert resp.status_code == 503

    def test_signed_token_is_accepted_once_and_rejected_on_reuse(
        self, client, healer_env
    ) -> None:
        ticket_id = _proposed_ticket(healer_env)
        token = issue_action_token(ticket_id, ACTION_APPROVE)

        first = client.post(f"/healer/approve/{ticket_id}?t={token}")
        assert first.status_code == 200
        assert first.json()["actor"].startswith("approval_link:")

        second = client.post(f"/healer/approve/{ticket_id}?t={token}")
        assert second.status_code == 401
        assert "already been used" in second.json()["detail"]

    def test_expired_token_is_rejected(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        token = issue_action_token(ticket_id, ACTION_APPROVE, ttl_seconds=1)
        time.sleep(1.1)
        resp = client.post(f"/healer/approve/{ticket_id}?t={token}")
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

    def test_tampered_token_is_rejected(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        token = issue_action_token(ticket_id, ACTION_APPROVE)
        forged = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        resp = client.post(f"/healer/approve/{ticket_id}?t={forged}")
        assert resp.status_code == 401
        assert healer_env.get_ticket(ticket_id).status == TicketStatus.PROPOSED

    def test_malformed_token_is_rejected(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        resp = client.post(f"/healer/approve/{ticket_id}?t=not-a-token")
        assert resp.status_code == 401

    def test_token_for_another_ticket_is_rejected(self, client, healer_env) -> None:
        first = _proposed_ticket(healer_env)
        second = _proposed_ticket(healer_env)
        token = issue_action_token(first, ACTION_APPROVE)
        resp = client.post(f"/healer/approve/{second}?t={token}")
        assert resp.status_code == 401

    def test_view_token_cannot_approve(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        token = issue_action_token(ticket_id, ACTION_VIEW)
        resp = client.post(f"/healer/approve/{ticket_id}?t={token}")
        assert resp.status_code == 401

    def test_view_token_is_replayable(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        token = issue_action_token(ticket_id, ACTION_VIEW)
        assert client.get(f"/healer/tickets/{ticket_id}/view?t={token}").status_code == 200
        assert client.get(f"/healer/tickets/{ticket_id}/view?t={token}").status_code == 200


class TestActionTokenCodec:

    def test_round_trip(self) -> None:
        token = issue_action_token("ticket-1", ACTION_APPROVE, secret="s3cret")
        claims = verify_action_token(token, "ticket-1", ACTION_APPROVE, secret="s3cret")
        assert claims["tid"] == "ticket-1"
        assert claims["nonce"]

    def test_wrong_secret_rejected(self) -> None:
        token = issue_action_token("ticket-1", ACTION_APPROVE, secret="s3cret")
        with pytest.raises(AuthError) as exc:
            verify_action_token(token, "ticket-1", ACTION_APPROVE, secret="other")
        assert exc.value.status_code == 401

    def test_nonce_is_unique_per_token(self) -> None:
        a = verify_action_token(
            issue_action_token("t", ACTION_APPROVE, secret="s"), "t", ACTION_APPROVE, secret="s"
        )
        b = verify_action_token(
            issue_action_token("t", ACTION_APPROVE, secret="s"), "t", ACTION_APPROVE, secret="s"
        )
        assert a["nonce"] != b["nonce"]


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

class TestCsrf:

    def test_form_post_without_csrf_is_rejected(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        token = issue_action_token(ticket_id, ACTION_APPROVE)
        resp = client.post(
            f"/healer/approve/{ticket_id}/action",
            data={"token": token},
            headers=CONTROL_PLANE_AUTH,
        )
        assert resp.status_code == 403
        assert healer_env.get_ticket(ticket_id).status == TicketStatus.PROPOSED

    def test_view_page_embeds_csrf_and_action_token(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        page = client.get(
            f"/healer/tickets/{ticket_id}/view", headers=CONTROL_PLANE_AUTH
        ).text
        assert 'name="csrf"' in page
        assert 'name="token"' in page

    def test_form_post_with_page_credentials_succeeds(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        page = client.get(
            f"/healer/tickets/{ticket_id}/view", headers=CONTROL_PLANE_AUTH
        ).text
        csrf = _extract_input(page, "csrf")
        token = _extract_input(page, "token")
        resp = client.post(
            f"/healer/approve/{ticket_id}/action",
            data={"csrf": csrf, "token": token},
            headers=CONTROL_PLANE_AUTH,
        )
        assert resp.status_code == 200
        assert healer_env.get_ticket(ticket_id).status != TicketStatus.PROPOSED


def _extract_input(page: str, name: str) -> str:
    marker = f'name="{name}" value="'
    start = page.index(marker) + len(marker)
    return page[start:page.index('"', start)]


# ---------------------------------------------------------------------------
# Stored XSS
# ---------------------------------------------------------------------------

XSS = '<script>alert("pwn")</script>'


class TestOutputEscaping:

    def test_contract_fqn_from_ingest_is_escaped_in_the_view_page(
        self, client, healer_env
    ) -> None:
        client.post(
            "/healer/ingest",
            json={"source": "runtime", "contract_fqn": XSS, "error": "boom"},
            headers=DATA_PLANE_AUTH,
        )
        ticket_id = healer_env.list_tickets()[0].id
        page = client.get(
            f"/healer/tickets/{ticket_id}/view", headers=CONTROL_PLANE_AUTH
        ).text
        assert "<script>" not in page
        assert "&lt;script&gt;alert(&quot;pwn&quot;)&lt;/script&gt;" in page

    def test_raw_error_is_escaped(self, client, healer_env) -> None:
        client.post(
            "/healer/ingest",
            json={"source": "runtime", "error": f"failed {XSS}"},
            headers=DATA_PLANE_AUTH,
        )
        ticket_id = healer_env.list_tickets()[0].id
        page = client.get(
            f"/healer/tickets/{ticket_id}/view", headers=CONTROL_PLANE_AUTH
        ).text
        assert "<script>" not in page

    def test_proposal_change_values_are_escaped(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        healer_env.set_proposal(
            ticket_id,
            HealerProposal(
                contract_fqn="entity/test/task",
                before={},
                after=VALID_CONTRACT,
                changes=[{"change_type": "modified", "path": XSS, "new_value": XSS}],
                explanation=XSS,
                confidence=1.0,
                method="deterministic",
            ),
        )
        page = client.get(
            f"/healer/tickets/{ticket_id}/view", headers=CONTROL_PLANE_AUTH
        ).text
        assert "<script>" not in page

    def test_resolution_note_is_escaped(self, client, healer_env) -> None:
        ticket_id = _proposed_ticket(healer_env)
        healer_env.update_status(ticket_id, TicketStatus.FAILED, resolution_note=XSS)
        page = client.get(
            f"/healer/tickets/{ticket_id}/view", headers=CONTROL_PLANE_AUTH
        ).text
        assert "<script>" not in page


# ---------------------------------------------------------------------------
# Data plane
# ---------------------------------------------------------------------------

class TestDataPlane:

    def test_ingest_without_token_is_rejected(self, client, healer_env) -> None:
        resp = client.post("/healer/ingest", json={"source": "manual", "error": "x"})
        assert resp.status_code == 401
        assert healer_env.list_tickets() == []

    def test_ingest_with_wrong_token_is_rejected(self, client) -> None:
        resp = client.post(
            "/healer/ingest",
            json={"source": "manual", "error": "x"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    def test_status_without_token_is_rejected(self, client) -> None:
        assert client.get("/healer/status").status_code == 401

    def test_health_needs_no_credential(self, client) -> None:
        assert client.get("/healer/health").status_code == 200

    def test_ingest_fails_closed_when_unconfigured(self, client, monkeypatch) -> None:
        monkeypatch.delenv(INGEST_TOKEN_ENV, raising=False)
        resp = client.post(
            "/healer/ingest",
            json={"source": "manual", "error": "x"},
            headers=DATA_PLANE_AUTH,
        )
        assert resp.status_code == 503

    def test_ingest_is_rate_limited_per_contract(self, client, healer_env) -> None:
        srv._limiter = TokenBucketLimiter(rate_per_minute=0.0001, burst=2)
        payload = {"source": "runtime", "contract_fqn": "entity/test/task", "error": "boom"}
        codes = [
            client.post("/healer/ingest", json=payload, headers=DATA_PLANE_AUTH).status_code
            for _ in range(5)
        ]
        assert codes[:2] == [202, 202]
        assert codes[2:] == [429, 429, 429]
        assert len(healer_env.list_tickets()) == 2

    def test_rate_limit_is_scoped_to_the_failing_contract(self, client) -> None:
        srv._limiter = TokenBucketLimiter(rate_per_minute=0.0001, burst=1)
        noisy = {"source": "runtime", "contract_fqn": "entity/test/noisy", "error": "boom"}
        quiet = {"source": "runtime", "contract_fqn": "entity/test/quiet", "error": "boom"}
        assert client.post("/healer/ingest", json=noisy, headers=DATA_PLANE_AUTH).status_code == 202
        assert client.post("/healer/ingest", json=noisy, headers=DATA_PLANE_AUTH).status_code == 429
        assert client.post("/healer/ingest", json=quiet, headers=DATA_PLANE_AUTH).status_code == 202


class TestIngestDoesNotBlockTheEventLoop:

    def test_ingest_returns_before_the_pipeline_runs(self, healer_env, monkeypatch) -> None:
        """A slow pipeline must not delay the ingest response.

        The regression this guards: process_next() ran inline inside the async
        handler, so every reported error blocked the event loop for the length
        of an LLM round trip plus a full regeneration.
        """
        started = []

        def slow_process_next() -> bool:
            started.append(time.monotonic())
            time.sleep(1.0)
            return False

        monkeypatch.setattr(srv._pipeline, "process_next", slow_process_next)

        with TestClient(app) as client:
            began = time.monotonic()
            resp = client.post(
                "/healer/ingest",
                json={"source": "runtime", "error": "boom"},
                headers=DATA_PLANE_AUTH,
            )
            elapsed = time.monotonic() - began

            assert resp.status_code == 202
            assert elapsed < 0.5, f"ingest blocked for {elapsed:.2f}s"

            # The queue is still drained, just not on the request path.
            deadline = time.monotonic() + 5.0
            while not started and time.monotonic() < deadline:
                client.get("/healer/health")
                time.sleep(0.05)
            assert started, "background worker never drained the queue"

    def test_health_stays_responsive_during_a_slow_drain(self, healer_env, monkeypatch) -> None:
        def slow_process_next() -> bool:
            time.sleep(1.0)
            return False

        monkeypatch.setattr(srv._pipeline, "process_next", slow_process_next)

        with TestClient(app) as client:
            client.post(
                "/healer/ingest",
                json={"source": "runtime", "error": "boom"},
                headers=DATA_PLANE_AUTH,
            )
            time.sleep(0.2)
            began = time.monotonic()
            assert client.get("/healer/health").status_code == 200
            assert time.monotonic() - began < 0.5


# ---------------------------------------------------------------------------
# Applier
# ---------------------------------------------------------------------------

@pytest.fixture
def contract_path(tmp_path):
    directory = tmp_path / "domains" / "test" / "entities"
    directory.mkdir(parents=True)
    path = directory / "task.contract.yaml"
    path.write_text(yaml.dump(VALID_CONTRACT), encoding="utf-8")
    return path


class TestApplierSafety:

    def test_invalid_fix_leaves_the_file_byte_identical(self, contract_path, tmp_path) -> None:
        original = contract_path.read_bytes()
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={},
            after={"apiVersion": "wrong", "kind": "Entity",
                   "metadata": {"name": "task", "domain": "test"},
                   "requires": [], "spec": {"fields": {}}},
            changes=[],
            explanation="bad fix",
            confidence=0.5,
            method="llm_structural",
        )
        result = apply_fix(proposal, contract_path, diff_root=tmp_path / "diffs")
        assert result.success is False
        assert contract_path.read_bytes() == original

    def test_no_temp_files_are_left_behind_on_failure(self, contract_path, tmp_path) -> None:
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={},
            after={"apiVersion": "wrong"},
            changes=[],
            explanation="bad fix",
            confidence=0.5,
            method="llm_structural",
        )
        apply_fix(proposal, contract_path, diff_root=tmp_path / "diffs")
        assert list(contract_path.parent.iterdir()) == [contract_path]

    def test_missing_file_is_reported_not_raised(self, tmp_path) -> None:
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={},
            after=VALID_CONTRACT,
            changes=[],
            explanation="fix",
            confidence=1.0,
            method="deterministic",
        )
        result = apply_fix(
            proposal, tmp_path / "does_not_exist.yaml", diff_root=tmp_path / "diffs"
        )
        assert result.success is False
        assert "not found" in result.error

    def test_source_path_is_never_written_to_the_contract(
        self, contract_path, tmp_path
    ) -> None:
        loaded = dict(VALID_CONTRACT)
        loaded["_source_path"] = "/home/someone/private/domains/test/entities/task.yaml"
        loaded["metadata"] = {"name": "task", "domain": "test", "_internal": "x"}
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={"_source_path": "/tmp/x"},
            after=loaded,
            changes=[],
            explanation="fix",
            confidence=1.0,
            method="deterministic",
        )
        result = apply_fix(proposal, contract_path, diff_root=tmp_path / "diffs")
        assert result.success is True

        written = contract_path.read_text(encoding="utf-8")
        assert "_source_path" not in written
        assert "_internal" not in written
        assert "/home/someone" not in written

    def test_actor_is_recorded_in_the_diff_audit_trail(self, contract_path, tmp_path) -> None:
        from forge.diff.store import DiffStore

        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={"metadata": {"name": "Task"}},
            after=VALID_CONTRACT,
            changes=[],
            explanation="fix",
            confidence=1.0,
            method="deterministic",
        )
        diff_root = tmp_path / "diffs"
        result = apply_fix(
            proposal, contract_path, diff_root=diff_root,
            ticket_id="abc123", actor="approval_link:oncall",
        )
        assert result.success is True

        diffs = DiffStore(root=diff_root).list_diffs()
        assert diffs
        assert "actor=approval_link:oncall" in diffs[0].origin_detail
        assert "ticket-abc123" in diffs[0].origin_detail


class TestContaminatedContractRepair:
    """A file already corrupted by the old applier must be repairable.

    validate_contract no longer launders underscore-prefixed keys, so such a
    file fails validation. The healer repairs it rather than refusing: the
    corruption came from our own bug.
    """

    def test_deterministic_proposer_proposes_removing_source_path(self) -> None:
        from healer.proposer.deterministic import propose_deterministic_fix

        contaminated = dict(VALID_CONTRACT)
        contaminated["_source_path"] = "/home/someone/domains/test/entities/task.yaml"

        proposal = propose_deterministic_fix("entity/test/task", contaminated)
        assert proposal is not None
        assert "_source_path" not in proposal.after
        assert any(c.path == "_source_path" for c in proposal.changes)

    def test_apply_repairs_the_file_and_records_the_repair(
        self, contract_path, tmp_path
    ) -> None:
        contaminated = dict(VALID_CONTRACT)
        contaminated["_source_path"] = "/home/someone/domains/test/entities/task.yaml"
        contract_path.write_text(yaml.dump(contaminated), encoding="utf-8")

        from healer.proposer.deterministic import propose_deterministic_fix

        proposal = propose_deterministic_fix("entity/test/task", contaminated)
        result = apply_fix(proposal, contract_path, diff_root=tmp_path / "diffs")

        assert result.success is True
        assert result.notes == [] or "underscore" in result.notes[0]
        assert "_source_path" not in contract_path.read_text(encoding="utf-8")


class TestStripInternalKeys:

    def test_removes_underscore_keys_at_every_depth(self) -> None:
        cleaned = strip_internal_keys(
            {
                "_source_path": "/x",
                "spec": {"_meta": 1, "fields": [{"_tmp": 2, "type": "string"}]},
            }
        )
        assert cleaned == {"spec": {"fields": [{"type": "string"}]}}

    def test_does_not_mutate_its_input(self) -> None:
        original = {"_source_path": "/x", "kind": "Entity"}
        strip_internal_keys(original)
        assert "_source_path" in original


# ---------------------------------------------------------------------------
# Queue concurrency
# ---------------------------------------------------------------------------

class TestQueueClaim:

    def test_claim_is_atomic_across_threads(self, tmp_path) -> None:
        import threading

        queue = HealerQueue(db_path=tmp_path / "healer.db")
        for _ in range(20):
            queue.enqueue(HealerTicket(source=TicketSource.VALIDATION, raw_error="x"))

        claimed: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            while True:
                ticket = queue.claim_next()
                if ticket is None:
                    return
                with lock:
                    claimed.append(ticket.id)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(claimed) == 20
        assert len(set(claimed)) == 20, "a ticket was claimed twice"

    def test_claim_marks_the_ticket_analyzing(self, tmp_path) -> None:
        queue = HealerQueue(db_path=tmp_path / "healer.db")
        ticket = HealerTicket(source=TicketSource.VALIDATION, raw_error="x")
        queue.enqueue(ticket)
        claimed = queue.claim_next()
        assert claimed is not None
        assert queue.get_ticket(ticket.id).status == TicketStatus.ANALYZING
        assert queue.claim_next() is None

    def test_wal_is_enabled(self, tmp_path) -> None:
        queue = HealerQueue(db_path=tmp_path / "healer.db")
        mode = queue._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_writes_from_another_thread_are_visible(self, tmp_path) -> None:
        import threading

        queue = HealerQueue(db_path=tmp_path / "healer.db")
        ticket = HealerTicket(source=TicketSource.VALIDATION, raw_error="x")
        thread = threading.Thread(target=queue.enqueue, args=(ticket,))
        thread.start()
        thread.join()
        assert queue.get_ticket(ticket.id) is not None

    def test_nonce_can_only_be_consumed_once(self, tmp_path) -> None:
        queue = HealerQueue(db_path=tmp_path / "healer.db")
        assert queue.consume_token_nonce("n1", "t1", "approve", "operator") is True
        assert queue.consume_token_nonce("n1", "t1", "approve", "operator") is False

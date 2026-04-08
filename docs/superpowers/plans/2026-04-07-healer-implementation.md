# Healer System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Healer — a self-healing pipeline that detects, diagnoses, and repairs contract errors from both the Forge compilation pipeline and generated app runtime, with a SQLite-backed priority queue, tiered autonomy, CLI commands, and a FastAPI HTTP service.

**Architecture:** Five-stage pipeline (Intake → Analyzer → Proposer → Applier → Notifier) connected by a SQLite priority queue. Tier 1 fixes are deterministic (auto-apply), Tier 2-3 use LLM (queue for approval). HTTP API + file watcher for error intake. Docker service for deployment.

**Tech Stack:** Python 3.10+, SQLite (stdlib), FastAPI, Click, Rich, pydantic, existing `forge.normalize`, `forge.error_display`, `forge.parser.validator`, `forge.diff.*`, `engine.engine`

**Spec:** `docs/superpowers/specs/2026-04-07-healer-design.md`
**Issue:** syndicalt/specora-core#4

---

## File Map

| File | Responsibility |
|------|---------------|
| `healer/models.py` | HealerTicket, HealerProposal, enums (TicketSource, TicketStatus, Priority) |
| `healer/queue.py` | SQLite-backed priority queue (enqueue, next, update, list, stats) |
| `healer/analyzer/classifier.py` | Error classification → tier + type + priority |
| `healer/analyzer/tracer.py` | Runtime stacktrace → contract FQN inference (LLM) |
| `healer/proposer/deterministic.py` | Tier 1: normalize_contract() fixes |
| `healer/proposer/llm_proposer.py` | Tier 2-3: LLM structural/runtime fixes |
| `healer/applier.py` | Validate → write → diff → rollback on failure |
| `healer/notifier.py` | Console + webhook + file notifications |
| `healer/monitor.py` | Success rates, recurring patterns, metrics |
| `healer/pipeline.py` | Orchestrate full pipeline: analyze → propose → apply → notify |
| `healer/watcher.py` | File watcher for .forge/healer/inbox/ |
| `healer/api/server.py` | FastAPI app (HTTP ingest + management endpoints) |
| `healer/cli/commands.py` | Click commands (fix, status, approve, reject, etc.) |
| `tests/test_healer/test_models.py` | Model serialization tests |
| `tests/test_healer/test_queue.py` | Queue CRUD + priority ordering tests |
| `tests/test_healer/test_classifier.py` | Error classification tests |
| `tests/test_healer/test_deterministic.py` | Tier 1 fix tests |
| `tests/test_healer/test_applier.py` | Apply + rollback tests |
| `tests/test_healer/test_pipeline.py` | End-to-end pipeline tests |
| `tests/test_healer/test_api.py` | HTTP endpoint tests |

---

### Task 1: Data Models

**Files:**
- Create: `healer/models.py`
- Create: `tests/test_healer/__init__.py`
- Create: `tests/test_healer/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_healer/test_models.py
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
        # Must be JSON-serializable
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/cheap/OneDrive/Documents/projects/specora-core && python -m pytest tests/test_healer/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'healer.models'`

- [ ] **Step 3: Implement models**

```python
# healer/models.py
"""Healer data models — tickets, proposals, and enums for the healing pipeline."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class TicketSource(str, Enum):
    VALIDATION = "validation"
    COMPILATION = "compilation"
    RUNTIME = "runtime"
    MANUAL = "manual"


class TicketStatus(str, Enum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    FAILED = "failed"
    REJECTED = "rejected"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Priority sort key — lower number = higher priority
PRIORITY_ORDER = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.MEDIUM: 2,
    Priority.LOW: 3,
}


class HealerProposal:
    """A proposed fix attached to a ticket."""

    def __init__(
        self,
        contract_fqn: str,
        before: dict,
        after: dict,
        changes: list,
        explanation: str,
        confidence: float,
        method: str,
    ) -> None:
        self.contract_fqn = contract_fqn
        self.before = before
        self.after = after
        self.changes = changes
        self.explanation = explanation
        self.confidence = confidence
        self.method = method

    def to_dict(self) -> dict:
        return {
            "contract_fqn": self.contract_fqn,
            "before": self.before,
            "after": self.after,
            "changes": [
                c.model_dump(mode="json") if hasattr(c, "model_dump") else c
                for c in self.changes
            ],
            "explanation": self.explanation,
            "confidence": self.confidence,
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HealerProposal:
        return cls(
            contract_fqn=d["contract_fqn"],
            before=d["before"],
            after=d["after"],
            changes=d.get("changes", []),
            explanation=d["explanation"],
            confidence=d["confidence"],
            method=d["method"],
        )


class HealerTicket:
    """The primary unit of work flowing through the healing pipeline."""

    def __init__(
        self,
        source: TicketSource,
        raw_error: str,
        id: str | None = None,
        contract_fqn: str | None = None,
        error_type: str = "",
        context: dict | None = None,
        status: TicketStatus = TicketStatus.QUEUED,
        tier: int = 0,
        priority: Priority = Priority.MEDIUM,
        proposal: HealerProposal | None = None,
        created_at: datetime | None = None,
        resolved_at: datetime | None = None,
        resolution_note: str = "",
    ) -> None:
        self.id = id or str(uuid.uuid4())
        self.source = source
        self.contract_fqn = contract_fqn
        self.error_type = error_type
        self.raw_error = raw_error
        self.context = context or {}
        self.status = status
        self.tier = tier
        self.priority = priority
        self.proposal = proposal
        self.created_at = created_at or datetime.now(timezone.utc)
        self.resolved_at = resolved_at
        self.resolution_note = resolution_note

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "source": self.source.value,
            "contract_fqn": self.contract_fqn,
            "error_type": self.error_type,
            "raw_error": self.raw_error,
            "context": self.context,
            "status": self.status.value,
            "tier": self.tier,
            "priority": self.priority.value,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_note": self.resolution_note,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> HealerTicket:
        proposal_data = d.get("proposal")
        proposal = HealerProposal.from_dict(proposal_data) if proposal_data else None
        return cls(
            id=d["id"],
            source=TicketSource(d["source"]),
            contract_fqn=d.get("contract_fqn"),
            error_type=d.get("error_type", ""),
            raw_error=d["raw_error"],
            context=d.get("context", {}),
            status=TicketStatus(d["status"]),
            tier=d.get("tier", 0),
            priority=Priority(d.get("priority", "medium")),
            proposal=proposal,
            created_at=datetime.fromisoformat(d["created_at"]),
            resolved_at=datetime.fromisoformat(d["resolved_at"]) if d.get("resolved_at") else None,
            resolution_note=d.get("resolution_note", ""),
        )
```

Also create `tests/test_healer/__init__.py` (empty file).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_healer/test_models.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/models.py tests/test_healer/__init__.py tests/test_healer/test_models.py
git commit -m "feat(#4/T1): healer data models — HealerTicket, HealerProposal, enums"
```

---

### Task 2: SQLite Queue

**Files:**
- Create: `healer/queue.py`
- Create: `tests/test_healer/test_queue.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_healer/test_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'healer.queue'`

- [ ] **Step 3: Implement queue**

```python
# healer/queue.py
"""SQLite-backed priority queue for healer tickets."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from healer.models import (
    HealerProposal,
    HealerTicket,
    Priority,
    PRIORITY_ORDER,
    TicketSource,
    TicketStatus,
)


class HealerQueue:
    """SQLite-backed priority queue for HealerTickets.

    Stores tickets in a single SQLite database. Priority ordering
    uses a numeric sort key (CRITICAL=0, HIGH=1, MEDIUM=2, LOW=3)
    with FIFO within each priority level.
    """

    def __init__(self, db_path: Path | str = ".forge/healer/healer.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                contract_fqn TEXT,
                error_type TEXT DEFAULT '',
                raw_error TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'queued',
                tier INTEGER DEFAULT 0,
                priority TEXT NOT NULL DEFAULT 'medium',
                priority_order INTEGER NOT NULL DEFAULT 2,
                proposal TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution_note TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_status_priority ON tickets(status, priority_order, created_at);
            CREATE INDEX IF NOT EXISTS idx_contract_fqn ON tickets(contract_fqn);
        """)
        self._conn.commit()

    def enqueue(self, ticket: HealerTicket) -> str:
        d = ticket.to_dict()
        self._conn.execute(
            """INSERT INTO tickets
               (id, source, contract_fqn, error_type, raw_error, context,
                status, tier, priority, priority_order, proposal, created_at,
                resolved_at, resolution_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d["id"], d["source"], d["contract_fqn"], d["error_type"],
                d["raw_error"], json.dumps(d["context"]),
                d["status"], d["tier"], d["priority"],
                PRIORITY_ORDER.get(ticket.priority, 2),
                json.dumps(d["proposal"]) if d["proposal"] else None,
                d["created_at"], d["resolved_at"], d["resolution_note"],
            ),
        )
        self._conn.commit()
        return ticket.id

    def get_ticket(self, ticket_id: str) -> Optional[HealerTicket]:
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_ticket(row)

    def next_queued(self) -> Optional[HealerTicket]:
        row = self._conn.execute(
            """SELECT * FROM tickets
               WHERE status = 'queued'
               ORDER BY priority_order ASC, created_at ASC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return self._row_to_ticket(row)

    def update_status(
        self,
        ticket_id: str,
        status: TicketStatus,
        resolution_note: str = "",
    ) -> None:
        resolved_at = None
        if status in (TicketStatus.APPLIED, TicketStatus.FAILED, TicketStatus.REJECTED):
            resolved_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE tickets
               SET status = ?, resolution_note = ?, resolved_at = COALESCE(?, resolved_at)
               WHERE id = ?""",
            (status.value, resolution_note, resolved_at, ticket_id),
        )
        self._conn.commit()

    def set_proposal(self, ticket_id: str, proposal: HealerProposal) -> None:
        self._conn.execute(
            "UPDATE tickets SET proposal = ? WHERE id = ?",
            (json.dumps(proposal.to_dict()), ticket_id),
        )
        self._conn.commit()

    def list_tickets(
        self,
        status: Optional[TicketStatus] = None,
        priority: Optional[Priority] = None,
        contract_fqn: Optional[str] = None,
    ) -> list[HealerTicket]:
        query = "SELECT * FROM tickets WHERE 1=1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if priority:
            query += " AND priority = ?"
            params.append(priority.value)
        if contract_fqn:
            query += " AND contract_fqn = ?"
            params.append(contract_fqn)
        query += " ORDER BY priority_order ASC, created_at ASC"

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_ticket(r) for r in rows]

    def stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tickets GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["cnt"] for r in rows}
        total = sum(by_status.values())
        return {"by_status": by_status, "total": total}

    def _row_to_ticket(self, row: sqlite3.Row) -> HealerTicket:
        proposal_json = row["proposal"]
        proposal = HealerProposal.from_dict(json.loads(proposal_json)) if proposal_json else None
        return HealerTicket(
            id=row["id"],
            source=TicketSource(row["source"]),
            contract_fqn=row["contract_fqn"],
            error_type=row["error_type"] or "",
            raw_error=row["raw_error"],
            context=json.loads(row["context"]),
            status=TicketStatus(row["status"]),
            tier=row["tier"] or 0,
            priority=Priority(row["priority"]),
            proposal=proposal,
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
            resolution_note=row["resolution_note"] or "",
        )

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_healer/test_queue.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/queue.py tests/test_healer/test_queue.py
git commit -m "feat(#4/T2): SQLite-backed priority queue for healer tickets"
```

---

### Task 3: Error Classifier

**Files:**
- Create: `healer/analyzer/classifier.py`
- Create: `tests/test_healer/test_classifier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_healer/test_classifier.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_healer/test_classifier.py -v`
Expected: FAIL

- [ ] **Step 3: Implement classifier**

```python
# healer/analyzer/classifier.py
"""Error classification — assign tier, type, and priority to errors."""
from __future__ import annotations

import re
from dataclasses import dataclass

from forge.parser.validator import ContractValidationError
from healer.models import Priority


@dataclass
class Classification:
    """Result of classifying an error."""
    error_type: str
    tier: int
    priority: Priority


# Patterns for Tier 1 (deterministic) validation errors
_TIER1_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"does not match '\^\[a-z\]\[a-z0-9_\]\*\$'"), "naming"),
    (re.compile(r"does not match '\^\(entity\|workflow"), "fqn_format"),
    (re.compile(r"does not match '\^\[A-Z\]\[A-Z0-9_\]\*\$'"), "graph_edge"),
    (re.compile(r"does not match '\^\[A-Z\]\{2,6\}\$'"), "number_prefix"),
]

# Patterns for Tier 2 (structural) validation errors
_TIER2_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"is a required property"), "missing_field"),
    (re.compile(r"is not valid under any of the given schemas"), "schema_mismatch"),
    (re.compile(r"is not one of"), "invalid_enum"),
]


def classify_validation_error(err: ContractValidationError) -> Classification:
    """Classify a contract validation error into tier, type, and priority."""
    msg = err.message

    for pattern, error_type in _TIER1_PATTERNS:
        if pattern.search(msg):
            return Classification(error_type=error_type, tier=1, priority=Priority.HIGH)

    for pattern, error_type in _TIER2_PATTERNS:
        if pattern.search(msg):
            return Classification(error_type=error_type, tier=2, priority=Priority.HIGH)

    # Default: structural, tier 2
    return Classification(error_type="structural", tier=2, priority=Priority.MEDIUM)


def classify_raw_error(source: str, error: str, context: dict) -> Classification:
    """Classify a raw error string (from runtime or compilation)."""
    if source == "runtime":
        status = context.get("status_code", 0)
        if status >= 500:
            return Classification(error_type="runtime_500", tier=3, priority=Priority.CRITICAL)
        return Classification(error_type="runtime_exception", tier=3, priority=Priority.HIGH)

    if source == "compilation":
        if "unresolved reference" in error.lower():
            return Classification(error_type="missing_reference", tier=2, priority=Priority.HIGH)
        if "cycle" in error.lower():
            return Classification(error_type="dependency_cycle", tier=2, priority=Priority.CRITICAL)
        return Classification(error_type="compilation_error", tier=2, priority=Priority.HIGH)

    return Classification(error_type="unknown", tier=2, priority=Priority.MEDIUM)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_healer/test_classifier.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/analyzer/classifier.py tests/test_healer/test_classifier.py
git commit -m "feat(#4/T3): error classifier — tier, type, priority assignment"
```

---

### Task 4: Deterministic Proposer (Tier 1)

**Files:**
- Create: `healer/proposer/deterministic.py`
- Create: `tests/test_healer/test_deterministic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_healer/test_deterministic.py
"""Tests for healer.proposer.deterministic — Tier 1 normalize fixes."""
import copy

import pytest

from healer.proposer.deterministic import propose_deterministic_fix


class TestDeterministicFix:

    @pytest.fixture
    def broken_contract(self) -> dict:
        return {
            "apiVersion": "specora.dev/v1",
            "kind": "Entity",
            "metadata": {"name": "Task", "domain": "todo_list"},
            "requires": ["mixin/stdlib/timestamped", "todo_list/User"],
            "spec": {
                "fields": {
                    "assigned_to": {
                        "type": "uuid",
                        "references": {
                            "entity": "todo_list/User",
                            "graph_edge": "assigned_to",
                        },
                    },
                },
            },
        }

    def test_proposes_fix(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix(
            contract_fqn="entity/todo_list/task",
            contract=broken_contract,
        )
        assert proposal is not None
        assert proposal.confidence == 1.0
        assert proposal.method == "deterministic"

    def test_fixes_metadata_name(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        assert proposal.after["metadata"]["name"] == "task"

    def test_fixes_requires_fqn(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        assert "entity/todo_list/user" in proposal.after["requires"]

    def test_fixes_graph_edge(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        edge = proposal.after["spec"]["fields"]["assigned_to"]["references"]["graph_edge"]
        assert edge == "ASSIGNED_TO"

    def test_returns_none_if_already_valid(self) -> None:
        valid = {
            "apiVersion": "specora.dev/v1",
            "kind": "Entity",
            "metadata": {"name": "task", "domain": "todo_list"},
            "requires": ["mixin/stdlib/timestamped"],
            "spec": {"fields": {"name": {"type": "string"}}},
        }
        proposal = propose_deterministic_fix("entity/todo_list/task", valid)
        assert proposal is None

    def test_has_changes_list(self, broken_contract: dict) -> None:
        proposal = propose_deterministic_fix("entity/todo_list/task", broken_contract)
        assert proposal is not None
        assert len(proposal.changes) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_healer/test_deterministic.py -v`
Expected: FAIL

- [ ] **Step 3: Implement deterministic proposer**

```python
# healer/proposer/deterministic.py
"""Tier 1 proposer — deterministic fixes via normalize_contract()."""
from __future__ import annotations

import copy
from typing import Optional

from forge.diff.tracker import compute_diff
from forge.normalize import normalize_contract
from healer.models import HealerProposal


def propose_deterministic_fix(
    contract_fqn: str,
    contract: dict,
) -> Optional[HealerProposal]:
    """Attempt to fix a contract using deterministic normalization.

    Returns a HealerProposal if normalization changed the contract,
    or None if the contract was already normalized.
    """
    before = copy.deepcopy(contract)
    after = copy.deepcopy(contract)
    normalize_contract(after)

    changes = compute_diff(before, after)
    if not changes:
        return None

    change_descriptions = []
    for c in changes:
        if c.change_type == "modified":
            change_descriptions.append(f"{c.path}: {c.old_value!r} → {c.new_value!r}")

    explanation = "Deterministic normalization: " + "; ".join(change_descriptions[:5])
    if len(change_descriptions) > 5:
        explanation += f" (and {len(change_descriptions) - 5} more)"

    return HealerProposal(
        contract_fqn=contract_fqn,
        before=before,
        after=after,
        changes=changes,
        explanation=explanation,
        confidence=1.0,
        method="deterministic",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_healer/test_deterministic.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/proposer/deterministic.py tests/test_healer/test_deterministic.py
git commit -m "feat(#4/T4): deterministic proposer — Tier 1 normalize fixes"
```

---

### Task 5: Applier with Rollback

**Files:**
- Create: `healer/applier.py`
- Create: `tests/test_healer/test_applier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_healer/test_applier.py
"""Tests for healer.applier — apply fixes with rollback."""
import yaml
from pathlib import Path

import pytest

from healer.applier import apply_fix, ApplyResult
from healer.models import HealerProposal


@pytest.fixture
def domain_dir(tmp_path: Path) -> Path:
    d = tmp_path / "domains" / "test"
    d.mkdir(parents=True)
    (d / "entities").mkdir()
    return d


@pytest.fixture
def contract_path(domain_dir: Path) -> Path:
    p = domain_dir / "entities" / "task.contract.yaml"
    contract = {
        "apiVersion": "specora.dev/v1",
        "kind": "Entity",
        "metadata": {"name": "Task", "domain": "test"},
        "requires": [],
        "spec": {"fields": {"name": {"type": "string", "required": True}}},
    }
    p.write_text(yaml.dump(contract), encoding="utf-8")
    return p


class TestApplyFix:

    def test_applies_valid_fix(self, contract_path: Path, tmp_path: Path) -> None:
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={"metadata": {"name": "Task"}},
            after={
                "apiVersion": "specora.dev/v1",
                "kind": "Entity",
                "metadata": {"name": "task", "domain": "test"},
                "requires": [],
                "spec": {"fields": {"name": {"type": "string", "required": True}}},
            },
            changes=[],
            explanation="Fixed name",
            confidence=1.0,
            method="deterministic",
        )
        result = apply_fix(proposal, contract_path, diff_root=tmp_path / ".forge" / "diffs")
        assert result.success is True
        # Verify file was updated
        content = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert content["metadata"]["name"] == "task"

    def test_rollback_on_invalid_fix(self, contract_path: Path, tmp_path: Path) -> None:
        original = contract_path.read_text(encoding="utf-8")
        proposal = HealerProposal(
            contract_fqn="entity/test/task",
            before={},
            after={"apiVersion": "wrong", "kind": "Entity", "metadata": {"name": "task", "domain": "test"}, "requires": [], "spec": {"fields": {}}},
            changes=[],
            explanation="Bad fix",
            confidence=0.5,
            method="llm_structural",
        )
        result = apply_fix(proposal, contract_path, diff_root=tmp_path / ".forge" / "diffs")
        assert result.success is False
        assert "apiVersion" in result.error or "specora.dev/v1" in result.error
        # File should be restored
        assert contract_path.read_text(encoding="utf-8") == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_healer/test_applier.py -v`
Expected: FAIL

- [ ] **Step 3: Implement applier**

```python
# healer/applier.py
"""Apply healer fixes with validation and rollback."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from forge.diff.models import DiffOrigin
from forge.diff.store import DiffStore
from forge.diff.tracker import create_diff
from forge.parser.validator import validate_contract
from healer.models import HealerProposal

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Result of attempting to apply a fix."""
    success: bool
    error: str = ""


def apply_fix(
    proposal: HealerProposal,
    contract_path: Path,
    diff_root: Path = Path(".forge/diffs"),
    ticket_id: str = "",
) -> ApplyResult:
    """Apply a proposed fix to a contract file.

    1. Snapshot the original file content
    2. Write the proposed fix
    3. Validate the result
    4. On success: record diff, return success
    5. On failure: restore original, return failure
    """
    # Snapshot original
    original_content = contract_path.read_text(encoding="utf-8")

    # Write proposed fix
    new_content = yaml.dump(
        proposal.after, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    contract_path.write_text(new_content, encoding="utf-8")

    # Validate
    errors = validate_contract(proposal.after)
    real_errors = [e for e in errors if e.severity == "error"]

    if real_errors:
        # Rollback
        contract_path.write_text(original_content, encoding="utf-8")
        error_msgs = "; ".join(e.message for e in real_errors[:3])
        logger.warning("Fix failed validation, rolling back: %s", error_msgs)
        return ApplyResult(success=False, error=error_msgs)

    # Record diff
    diff = create_diff(
        contract_fqn=proposal.contract_fqn,
        before=proposal.before,
        after=proposal.after,
        origin=DiffOrigin.HEALER,
        origin_detail=f"healer:ticket-{ticket_id}" if ticket_id else "healer:direct",
        reason=proposal.explanation,
    )
    store = DiffStore(root=diff_root)
    store.save(diff)

    logger.info("Applied fix to %s (%s)", proposal.contract_fqn, proposal.method)
    return ApplyResult(success=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_healer/test_applier.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/applier.py tests/test_healer/test_applier.py
git commit -m "feat(#4/T5): applier with validation and rollback"
```

---

### Task 6: Notifier

**Files:**
- Create: `healer/notifier.py`
- Create: `tests/test_healer/test_notifier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_healer/test_notifier.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_healer/test_notifier.py -v`
Expected: FAIL

- [ ] **Step 3: Implement notifier**

```python
# healer/notifier.py
"""Notification channels — console, webhook, file."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console

from healer.models import HealerTicket

logger = logging.getLogger(__name__)
console = Console()


class Notifier:
    """Multi-channel notification for healer events."""

    def __init__(
        self,
        log_path: Path = Path(".forge/healer/notifications.jsonl"),
        webhook_url: Optional[str] = None,
    ) -> None:
        self.log_path = Path(log_path)
        self.webhook_url = webhook_url or os.environ.get("SPECORA_HEALER_WEBHOOK_URL")

    def notify(
        self,
        ticket: HealerTicket,
        event: str,
        message: str = "",
    ) -> None:
        """Send notification across all channels."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "ticket_id": ticket.id,
            "contract_fqn": ticket.contract_fqn,
            "status": ticket.status.value,
            "tier": ticket.tier,
            "priority": ticket.priority.value,
            "message": message or ticket.raw_error[:200],
        }

        self._log_to_file(payload)
        self._log_to_console(payload)
        if self.webhook_url:
            self._send_webhook(payload)

    def _log_to_file(self, payload: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")

    def _log_to_console(self, payload: dict) -> None:
        event = payload["event"]
        fqn = payload.get("contract_fqn") or "unknown"
        colors = {"queued": "yellow", "applied": "green", "failed": "red", "proposed": "cyan", "rejected": "red"}
        color = colors.get(event, "white")
        console.print(f"[{color}][healer/{event}][/{color}] {fqn}: {payload.get('message', '')[:80]}")

    def _send_webhook(self, payload: dict) -> None:
        try:
            httpx.post(self.webhook_url, json=payload, timeout=5.0)
        except Exception as e:
            logger.warning("Webhook failed: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_healer/test_notifier.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/notifier.py tests/test_healer/test_notifier.py
git commit -m "feat(#4/T6): notifier — console, webhook, and file notifications"
```

---

### Task 7: Pipeline Orchestrator

**Files:**
- Create: `healer/pipeline.py`
- Create: `tests/test_healer/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_healer/test_pipeline.py
"""Tests for healer.pipeline — end-to-end pipeline orchestration."""
import yaml
from pathlib import Path

import pytest

from healer.models import HealerTicket, TicketSource, TicketStatus, Priority
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue


@pytest.fixture
def setup(tmp_path: Path):
    """Create queue, domain dir, and pipeline."""
    queue = HealerQueue(db_path=tmp_path / "healer.db")

    # Create a broken contract on disk
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

        # Create ticket for a naming error
        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error="'Task' does not match '^[a-z][a-z0-9_]*$'",
            contract_fqn="entity/todo_list/task",
            context={"source_path": str(contract_path)},
        )
        queue.enqueue(ticket)

        # Process one ticket
        processed = pipeline.process_next()
        assert processed is True

        # Ticket should be applied
        result = queue.get_ticket(ticket.id)
        assert result is not None
        assert result.status == TicketStatus.APPLIED

        # Contract should be fixed on disk
        content = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert content["metadata"]["name"] == "task"

    def test_returns_false_when_empty(self, setup) -> None:
        _, pipeline, _ = setup
        assert pipeline.process_next() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_healer/test_pipeline.py -v`
Expected: FAIL

- [ ] **Step 3: Implement pipeline**

```python
# healer/pipeline.py
"""Pipeline orchestrator — analyze, propose, apply, notify."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from forge.parser.loader import load_contract
from forge.parser.validator import validate_contract
from healer.analyzer.classifier import classify_validation_error, classify_raw_error, Classification
from healer.applier import apply_fix, ApplyResult
from healer.models import HealerTicket, TicketSource, TicketStatus, Priority
from healer.notifier import Notifier
from healer.proposer.deterministic import propose_deterministic_fix
from healer.queue import HealerQueue

logger = logging.getLogger(__name__)


class HealerPipeline:
    """Orchestrates the full healing pipeline."""

    def __init__(
        self,
        queue: HealerQueue,
        domains_root: Path = Path("domains"),
        diff_root: Path = Path(".forge/diffs"),
        log_path: Path = Path(".forge/healer/notifications.jsonl"),
    ) -> None:
        self.queue = queue
        self.domains_root = domains_root
        self.diff_root = diff_root
        self.notifier = Notifier(log_path=log_path)

    def process_next(self) -> bool:
        """Process the next queued ticket. Returns True if a ticket was processed."""
        ticket = self.queue.next_queued()
        if ticket is None:
            return False

        self.queue.update_status(ticket.id, TicketStatus.ANALYZING)
        self._process_ticket(ticket)
        return True

    def _process_ticket(self, ticket: HealerTicket) -> None:
        # Stage 2: Classify
        classification = self._classify(ticket)
        ticket.error_type = classification.error_type
        ticket.tier = classification.tier
        ticket.priority = classification.priority

        # Stage 3: Propose
        proposal = self._propose(ticket)
        if proposal is None:
            self.queue.update_status(
                ticket.id, TicketStatus.FAILED,
                resolution_note="No fix could be proposed",
            )
            self.notifier.notify(ticket, event="failed", message="No fix proposed")
            return

        self.queue.set_proposal(ticket.id, proposal)
        ticket.proposal = proposal

        # Stage 4: Apply (Tier 1 auto-applies, Tier 2-3 queue for approval)
        if ticket.tier == 1:
            self._apply_and_notify(ticket)
        else:
            self.queue.update_status(ticket.id, TicketStatus.PROPOSED)
            self.notifier.notify(ticket, event="proposed", message=proposal.explanation)

    def approve_ticket(self, ticket_id: str) -> bool:
        """Approve and apply a proposed fix."""
        ticket = self.queue.get_ticket(ticket_id)
        if ticket is None or ticket.status != TicketStatus.PROPOSED:
            return False
        self.queue.update_status(ticket_id, TicketStatus.APPROVED)
        self._apply_and_notify(ticket)
        return True

    def reject_ticket(self, ticket_id: str, reason: str = "") -> bool:
        """Reject a proposed fix."""
        ticket = self.queue.get_ticket(ticket_id)
        if ticket is None or ticket.status != TicketStatus.PROPOSED:
            return False
        self.queue.update_status(ticket_id, TicketStatus.REJECTED, resolution_note=reason)
        self.notifier.notify(ticket, event="rejected", message=reason)
        return True

    def _classify(self, ticket: HealerTicket) -> Classification:
        if ticket.source == TicketSource.VALIDATION:
            from forge.parser.validator import ContractValidationError
            err = ContractValidationError(
                contract_fqn=ticket.contract_fqn or "",
                message=ticket.raw_error,
                path=ticket.context.get("path", ""),
            )
            return classify_validation_error(err)
        return classify_raw_error(
            source=ticket.source.value,
            error=ticket.raw_error,
            context=ticket.context,
        )

    def _propose(self, ticket: HealerTicket):
        if ticket.tier == 1 and ticket.contract_fqn:
            contract = self._load_contract(ticket.contract_fqn)
            if contract:
                return propose_deterministic_fix(ticket.contract_fqn, contract)
        # Tier 2-3: LLM proposer (deferred to Task 9)
        return None

    def _apply_and_notify(self, ticket: HealerTicket) -> None:
        if ticket.proposal is None:
            self.queue.update_status(ticket.id, TicketStatus.FAILED, resolution_note="No proposal")
            return

        contract_path = self._find_contract_path(ticket.contract_fqn or "")
        if contract_path is None:
            self.queue.update_status(
                ticket.id, TicketStatus.FAILED,
                resolution_note=f"Contract file not found for {ticket.contract_fqn}",
            )
            self.notifier.notify(ticket, event="failed", message="Contract file not found")
            return

        result = apply_fix(
            ticket.proposal, contract_path,
            diff_root=self.diff_root, ticket_id=ticket.id,
        )
        if result.success:
            self.queue.update_status(ticket.id, TicketStatus.APPLIED, resolution_note="Fix applied")
            self.notifier.notify(ticket, event="applied", message=ticket.proposal.explanation)
        else:
            self.queue.update_status(ticket.id, TicketStatus.FAILED, resolution_note=result.error)
            self.notifier.notify(ticket, event="failed", message=result.error)

    def _load_contract(self, fqn: str) -> Optional[dict]:
        """Load a contract dict from disk by FQN."""
        path = self._find_contract_path(fqn)
        if path and path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        return None

    def _find_contract_path(self, fqn: str) -> Optional[Path]:
        """Resolve FQN to file path. FQN format: kind/domain/name."""
        parts = fqn.split("/")
        if len(parts) != 3:
            return None
        kind, domain, name = parts
        kind_dirs = {
            "entity": "entities", "workflow": "workflows",
            "page": "pages", "route": "routes",
            "agent": "agents", "mixin": "mixins", "infra": "infra",
        }
        subdir = kind_dirs.get(kind, kind + "s")
        path = self.domains_root / domain / subdir / f"{name}.contract.yaml"
        return path if path.exists() else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_healer/test_pipeline.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/pipeline.py tests/test_healer/test_pipeline.py
git commit -m "feat(#4/T7): pipeline orchestrator — analyze, propose, apply, notify"
```

---

### Task 8: CLI Commands

**Files:**
- Create: `healer/cli/commands.py`
- Create: `healer/cli/__init__.py`
- Modify: `forge/cli/main.py` — register healer command group

- [ ] **Step 1: Implement CLI commands**

```python
# healer/cli/__init__.py
# (empty)
```

```python
# healer/cli/commands.py
"""Healer CLI commands — fix, status, approve, reject, history, serve."""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from forge.error_display import format_errors_rich
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all
from healer.models import HealerTicket, TicketSource, TicketStatus, Priority
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue

console = Console()


def _default_queue() -> HealerQueue:
    return HealerQueue(db_path=Path(".forge/healer/healer.db"))


def _default_pipeline(queue: HealerQueue | None = None) -> HealerPipeline:
    q = queue or _default_queue()
    return HealerPipeline(queue=q)


@click.group()
def healer() -> None:
    """The Healer — self-healing contract repair pipeline."""
    pass


@healer.command()
@click.argument("path", default="domains/", type=click.Path(exists=True))
def fix(path: str) -> None:
    """Validate contracts and auto-fix errors through the healing pipeline."""
    contracts = load_all_contracts(Path(path))
    errors = validate_all(contracts)

    if not errors:
        console.print(f"[green]All {len(contracts)} contracts are valid — nothing to heal[/green]")
        return

    console.print(format_errors_rich(errors))
    console.print()

    queue = _default_queue()
    pipeline = _default_pipeline(queue)
    created = 0

    for err in errors:
        if err.severity != "error":
            continue
        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error=err.message,
            contract_fqn=err.contract_fqn,
            context={"path": err.path, "source_path": err.source_path},
        )
        queue.enqueue(ticket)
        created += 1

    console.print(f"[yellow]Created {created} tickets[/yellow]")

    applied = 0
    while pipeline.process_next():
        applied += 1

    stats = queue.stats()
    console.print(f"[green]Processed {applied} tickets[/green]")
    if stats["by_status"].get("proposed", 0) > 0:
        console.print(
            f"[cyan]{stats['by_status']['proposed']} tickets awaiting approval[/cyan]"
            f" (use 'specora healer tickets' to view)"
        )


@healer.command()
def status() -> None:
    """Show queue summary."""
    queue = _default_queue()
    stats = queue.stats()

    table = Table(title="Healer Queue")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")

    for s in ["queued", "analyzing", "proposed", "approved", "applied", "failed", "rejected"]:
        count = stats["by_status"].get(s, 0)
        color = {"applied": "green", "failed": "red", "proposed": "cyan", "queued": "yellow"}.get(s, "white")
        table.add_row(f"[{color}]{s}[/{color}]", str(count))

    table.add_row("[bold]Total[/bold]", f"[bold]{stats['total']}[/bold]")
    console.print(table)


@healer.command()
@click.option("--status", "status_filter", type=click.Choice(["queued", "proposed", "applied", "failed", "rejected"]))
@click.option("--priority", type=click.Choice(["critical", "high", "medium", "low"]))
def tickets(status_filter: str | None, priority: str | None) -> None:
    """List tickets."""
    queue = _default_queue()
    s = TicketStatus(status_filter) if status_filter else None
    p = Priority(priority) if priority else None
    items = queue.list_tickets(status=s, priority=p)

    if not items:
        console.print("[dim]No tickets found[/dim]")
        return

    table = Table(title=f"Healer Tickets ({len(items)})")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Status")
    table.add_column("Priority")
    table.add_column("Tier", justify="right")
    table.add_column("Contract")
    table.add_column("Error", max_width=40)

    for t in items[:50]:
        table.add_row(
            t.id[:8], t.status.value, t.priority.value,
            str(t.tier), t.contract_fqn or "?", t.raw_error[:40],
        )
    console.print(table)


@healer.command()
@click.argument("ticket_id")
def show(ticket_id: str) -> None:
    """Show ticket detail."""
    queue = _default_queue()
    # Support short IDs
    items = queue.list_tickets()
    ticket = None
    for t in items:
        if t.id.startswith(ticket_id):
            ticket = t
            break
    if ticket is None:
        console.print(f"[red]Ticket not found: {ticket_id}[/red]")
        sys.exit(1)

    console.print(f"[bold]Ticket: {ticket.id}[/bold]")
    console.print(f"Status: {ticket.status.value} | Priority: {ticket.priority.value} | Tier: {ticket.tier}")
    console.print(f"Source: {ticket.source.value} | Contract: {ticket.contract_fqn or '?'}")
    console.print(f"Error: {ticket.raw_error}")
    if ticket.proposal:
        console.print(f"\n[bold]Proposed Fix:[/bold] {ticket.proposal.explanation}")
        console.print(f"Confidence: {ticket.proposal.confidence} | Method: {ticket.proposal.method}")
    if ticket.resolution_note:
        console.print(f"\n[bold]Resolution:[/bold] {ticket.resolution_note}")


@healer.command()
@click.argument("ticket_id")
def approve(ticket_id: str) -> None:
    """Approve a proposed fix."""
    queue = _default_queue()
    pipeline = _default_pipeline(queue)
    # Support short IDs
    items = queue.list_tickets(status=TicketStatus.PROPOSED)
    found = None
    for t in items:
        if t.id.startswith(ticket_id):
            found = t
            break
    if found is None:
        console.print(f"[red]No proposed ticket found: {ticket_id}[/red]")
        sys.exit(1)

    success = pipeline.approve_ticket(found.id)
    if success:
        console.print(f"[green]Approved and applied: {found.id[:8]}[/green]")
    else:
        console.print(f"[red]Failed to apply: {found.id[:8]}[/red]")


@healer.command()
@click.argument("ticket_id")
@click.option("--reason", "-r", default="", help="Reason for rejection")
def reject(ticket_id: str, reason: str) -> None:
    """Reject a proposed fix."""
    queue = _default_queue()
    pipeline = _default_pipeline(queue)
    items = queue.list_tickets(status=TicketStatus.PROPOSED)
    found = None
    for t in items:
        if t.id.startswith(ticket_id):
            found = t
            break
    if found is None:
        console.print(f"[red]No proposed ticket found: {ticket_id}[/red]")
        sys.exit(1)

    pipeline.reject_ticket(found.id, reason)
    console.print(f"[yellow]Rejected: {found.id[:8]}[/yellow]")


@healer.command()
def history() -> None:
    """Show applied healer fixes."""
    from forge.diff.models import DiffOrigin
    from forge.diff.store import DiffStore

    store = DiffStore(root=Path(".forge/diffs"))
    diffs = store.list_diffs(origin=DiffOrigin.HEALER)

    if not diffs:
        console.print("[dim]No healer fixes recorded[/dim]")
        return

    table = Table(title=f"Healer Fix History ({len(diffs)})")
    table.add_column("Date", style="cyan")
    table.add_column("Contract")
    table.add_column("Reason", max_width=50)
    table.add_column("Changes", justify="right")

    for d in diffs[:20]:
        table.add_row(
            d.timestamp.strftime("%Y-%m-%d %H:%M"),
            d.contract_fqn,
            d.reason[:50],
            str(len(d.changes)),
        )
    console.print(table)
```

- [ ] **Step 2: Register healer commands in main CLI**

Add to `forge/cli/main.py` after the factory registration block:

```python
# Import and register healer commands
from healer.cli.commands import healer as healer_group
cli.add_command(healer_group, "healer")
```

- [ ] **Step 3: Manual verification**

Run: `cd C:/Users/cheap/OneDrive/Documents/projects/specora-core && python -m forge.cli.main healer --help`
Expected: Shows healer subcommands (fix, status, tickets, show, approve, reject, history)

Run: `python -m forge.cli.main healer status`
Expected: Shows empty queue table

- [ ] **Step 4: Commit**

```bash
git add healer/cli/__init__.py healer/cli/commands.py forge/cli/main.py
git commit -m "feat(#4/T8): CLI commands — fix, status, tickets, approve, reject, history"
```

---

### Task 9: LLM Proposer (Tier 2-3)

**Files:**
- Create: `healer/proposer/llm_proposer.py`
- Create: `healer/analyzer/tracer.py`
- Modify: `healer/pipeline.py` — wire LLM proposer into `_propose()`

- [ ] **Step 1: Implement tracer**

```python
# healer/analyzer/tracer.py
"""Runtime stacktrace → contract FQN inference."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# Map generated file paths to contract FQNs via @generated headers
_GENERATED_PATTERN = re.compile(r"@generated\s+from\s+([\w/]+)")


def trace_to_contract(
    stacktrace: str,
    context: dict,
    domains_root: Path = Path("domains"),
) -> Optional[str]:
    """Attempt to infer the source contract FQN from a runtime error.

    Strategy:
    1. Check context for explicit contract_fqn
    2. Check context for generated_file → read @generated header
    3. Parse stacktrace for generated file paths
    """
    # 1. Explicit FQN in context
    if context.get("contract_fqn"):
        return context["contract_fqn"]

    # 2. Generated file header
    generated_file = context.get("generated_file")
    if generated_file:
        fqn = _read_generated_header(Path(generated_file))
        if fqn:
            return fqn

    # 3. Parse stacktrace for runtime/ paths
    for match in re.finditer(r'File "([^"]*runtime[^"]*)"', stacktrace):
        fqn = _read_generated_header(Path(match.group(1)))
        if fqn:
            return fqn

    return None


def _read_generated_header(path: Path) -> Optional[str]:
    """Read the @generated provenance header from a file."""
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:500]
        match = _GENERATED_PATTERN.search(head)
        return match.group(1) if match else None
    except OSError:
        return None
```

- [ ] **Step 2: Implement LLM proposer**

```python
# healer/proposer/llm_proposer.py
"""Tier 2-3 proposer — LLM-powered structural and runtime fixes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from forge.diff.store import DiffStore
from forge.diff.tracker import compute_diff
from forge.parser.validator import validate_contract
from healer.models import HealerProposal, HealerTicket

logger = logging.getLogger(__name__)


def propose_llm_fix(
    ticket: HealerTicket,
    contract: dict,
    diff_root: Path = Path(".forge/diffs"),
) -> Optional[HealerProposal]:
    """Propose a fix using the LLM engine.

    Builds a prompt with the contract, errors, meta-schema context,
    and diff history, then asks the LLM to produce a corrected contract.
    """
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception as e:
        logger.warning("LLM engine not available: %s", e)
        return None

    # Build context
    contract_yaml = yaml.dump(contract, default_flow_style=False, sort_keys=False)
    store = DiffStore(root=diff_root)
    diff_history = store.format_for_llm(ticket.contract_fqn or "", n=5)

    prompt = _build_prompt(ticket, contract_yaml, diff_history)

    try:
        response = engine.ask(
            question=prompt,
            system=_SYSTEM_PROMPT,
        )
    except Exception as e:
        logger.error("LLM request failed: %s", e)
        return None

    # Parse YAML from response
    proposed = _extract_yaml(response)
    if proposed is None:
        logger.warning("Could not parse YAML from LLM response")
        return None

    # Validate the proposal
    errors = validate_contract(proposed)
    real_errors = [e for e in errors if e.severity == "error"]
    if real_errors:
        logger.warning("LLM proposal has %d validation errors", len(real_errors))
        return None

    changes = compute_diff(contract, proposed)
    if not changes:
        return None

    method = "llm_runtime" if ticket.tier == 3 else "llm_structural"
    return HealerProposal(
        contract_fqn=ticket.contract_fqn or "",
        before=contract,
        after=proposed,
        changes=changes,
        explanation=_extract_explanation(response),
        confidence=0.7 if ticket.tier == 2 else 0.5,
        method=method,
    )


_SYSTEM_PROMPT = """You are a contract healing expert for the Specora CDD engine.
You receive a broken contract (YAML) and its validation/runtime errors.
You must output a corrected version of the contract as valid YAML.

Rules:
- metadata.name must be snake_case (^[a-z][a-z0-9_]*$)
- requires entries must be FQN format: kind/domain/name, all lowercase
- graph_edge must be SCREAMING_SNAKE_CASE (^[A-Z][A-Z0-9_]*$)
- Do not remove fields unless they are the cause of the error
- Preserve the contract's intent — fix the form, not the meaning

Output format:
1. A brief explanation of what you changed and why (1-2 sentences)
2. The corrected contract as a YAML code block (```yaml ... ```)
"""


def _build_prompt(ticket: HealerTicket, contract_yaml: str, diff_history: str) -> str:
    parts = [
        f"Contract FQN: {ticket.contract_fqn}",
        f"\nError:\n{ticket.raw_error}",
        f"\nCurrent contract:\n```yaml\n{contract_yaml}```",
    ]
    if diff_history and "No change history" not in diff_history:
        parts.append(f"\nRecent change history:\n{diff_history}")
    if ticket.tier == 3 and ticket.context.get("stacktrace"):
        parts.append(f"\nRuntime stacktrace:\n{ticket.context['stacktrace']}")
    return "\n".join(parts)


def _extract_yaml(response: str) -> Optional[dict]:
    """Extract YAML from a code block in the LLM response."""
    import re
    match = re.search(r"```ya?ml\s*\n(.*?)```", response, re.DOTALL)
    if match:
        try:
            return yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None
    # Try parsing the whole response as YAML
    try:
        return yaml.safe_load(response)
    except yaml.YAMLError:
        return None


def _extract_explanation(response: str) -> str:
    """Extract the explanation text before the YAML block."""
    import re
    match = re.search(r"```", response)
    if match:
        return response[:match.start()].strip()[:200]
    return response[:200].strip()
```

- [ ] **Step 3: Wire LLM proposer into pipeline**

Edit `healer/pipeline.py`, replace the `_propose` method:

```python
    def _propose(self, ticket: HealerTicket):
        if ticket.contract_fqn:
            contract = self._load_contract(ticket.contract_fqn)
            if contract:
                if ticket.tier == 1:
                    return propose_deterministic_fix(ticket.contract_fqn, contract)
                else:
                    from healer.proposer.llm_proposer import propose_llm_fix
                    return propose_llm_fix(ticket, contract, diff_root=self.diff_root)
        return None
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add healer/analyzer/tracer.py healer/proposer/llm_proposer.py healer/pipeline.py
git commit -m "feat(#4/T9): LLM proposer + runtime tracer for Tier 2-3 fixes"
```

---

### Task 10: FastAPI HTTP Service

**Files:**
- Create: `healer/api/__init__.py`
- Create: `healer/api/server.py`
- Create: `tests/test_healer/test_api.py`
- Modify: `healer/cli/commands.py` — add `serve` command
- Modify: `pyproject.toml` — add `fastapi` and `uvicorn` to optional deps

- [ ] **Step 1: Add dependencies to pyproject.toml**

Add to `[project.optional-dependencies]`:

```toml
healer = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "httpx>=0.27",
]
```

Update `all` to include healer:

```toml
all = [
    "specora-core[dev,llm,healer]",
]
```

- [ ] **Step 2: Implement FastAPI server**

```python
# healer/api/__init__.py
# (empty)
```

```python
# healer/api/server.py
"""FastAPI HTTP service for the Healer."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from healer.models import HealerTicket, TicketSource, TicketStatus, Priority
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue

app = FastAPI(title="Specora Healer", version="0.1.0")

_queue: Optional[HealerQueue] = None
_pipeline: Optional[HealerPipeline] = None


def get_queue() -> HealerQueue:
    global _queue
    if _queue is None:
        _queue = HealerQueue()
    return _queue


def get_pipeline() -> HealerPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = HealerPipeline(queue=get_queue())
    return _pipeline


class IngestRequest(BaseModel):
    source: str = "manual"
    contract_fqn: Optional[str] = None
    error: str
    stacktrace: str = ""
    context: dict = {}


class IngestResponse(BaseModel):
    ticket_id: str
    status: str


class TicketResponse(BaseModel):
    id: str
    source: str
    contract_fqn: Optional[str]
    error_type: str
    raw_error: str
    status: str
    tier: int
    priority: str
    created_at: str
    resolved_at: Optional[str]
    resolution_note: str


class RejectRequest(BaseModel):
    reason: str = ""


@app.post("/healer/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest):
    queue = get_queue()
    ctx = req.context.copy()
    if req.stacktrace:
        ctx["stacktrace"] = req.stacktrace

    ticket = HealerTicket(
        source=TicketSource(req.source) if req.source in [s.value for s in TicketSource] else TicketSource.MANUAL,
        raw_error=req.error,
        contract_fqn=req.contract_fqn,
        context=ctx,
    )
    queue.enqueue(ticket)

    # Process immediately
    pipeline = get_pipeline()
    pipeline.process_next()

    # Refresh ticket
    updated = queue.get_ticket(ticket.id)
    return IngestResponse(
        ticket_id=ticket.id,
        status=updated.status.value if updated else "queued",
    )


@app.get("/healer/tickets")
def list_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    contract_fqn: Optional[str] = None,
):
    queue = get_queue()
    s = TicketStatus(status) if status else None
    p = Priority(priority) if priority else None
    items = queue.list_tickets(status=s, priority=p, contract_fqn=contract_fqn)
    return [_ticket_to_response(t) for t in items]


@app.get("/healer/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    queue = get_queue()
    ticket = queue.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    resp = _ticket_to_response(ticket)
    if ticket.proposal:
        resp["proposal"] = ticket.proposal.to_dict()
    return resp


@app.post("/healer/approve/{ticket_id}")
def approve_ticket(ticket_id: str):
    pipeline = get_pipeline()
    success = pipeline.approve_ticket(ticket_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot approve this ticket")
    return {"status": "applied"}


@app.post("/healer/reject/{ticket_id}")
def reject_ticket(ticket_id: str, req: RejectRequest):
    pipeline = get_pipeline()
    success = pipeline.reject_ticket(ticket_id, reason=req.reason)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot reject this ticket")
    return {"status": "rejected"}


@app.get("/healer/status")
def queue_status():
    queue = get_queue()
    return queue.stats()


@app.get("/healer/health")
def health():
    return {"status": "ok", "service": "healer"}


def _ticket_to_response(t: HealerTicket) -> dict:
    return {
        "id": t.id,
        "source": t.source.value,
        "contract_fqn": t.contract_fqn,
        "error_type": t.error_type,
        "raw_error": t.raw_error[:200],
        "status": t.status.value,
        "tier": t.tier,
        "priority": t.priority.value,
        "created_at": t.created_at.isoformat(),
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "resolution_note": t.resolution_note,
    }
```

- [ ] **Step 3: Write API tests**

```python
# tests/test_healer/test_api.py
"""Tests for healer.api.server — HTTP endpoint contracts."""
import pytest
from fastapi.testclient import TestClient

from healer.api.server import app, _queue, _pipeline


@pytest.fixture(autouse=True)
def reset_globals(tmp_path):
    """Reset global state for each test."""
    import healer.api.server as srv
    from healer.queue import HealerQueue
    from healer.pipeline import HealerPipeline

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
        resp = client.post("/healer/ingest", json={
            "source": "manual",
            "error": "test error",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "ticket_id" in data
        assert data["status"] in ["queued", "failed"]

    def test_ingest_with_contract_fqn(self, client) -> None:
        resp = client.post("/healer/ingest", json={
            "source": "validation",
            "contract_fqn": "entity/test/task",
            "error": "'Task' does not match",
        })
        assert resp.status_code == 200


class TestEndpoints:

    def test_health(self, client) -> None:
        resp = client.get("/healer/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_status_empty(self, client) -> None:
        resp = client.get("/healer/status")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_tickets_empty(self, client) -> None:
        resp = client.get("/healer/tickets")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ticket_not_found(self, client) -> None:
        resp = client.get("/healer/tickets/nonexistent")
        assert resp.status_code == 404
```

- [ ] **Step 4: Add `serve` command to CLI**

Add to `healer/cli/commands.py`:

```python
@healer.command()
@click.option("--port", default=8083, help="Port to serve on")
@click.option("--host", default="0.0.0.0", help="Host to bind to")
def serve(port: int, host: str) -> None:
    """Start the Healer HTTP service."""
    import uvicorn
    from healer.api.server import app
    console.print(f"[bold]Starting Healer service on {host}:{port}[/bold]")
    uvicorn.run(app, host=host, port=port)
```

- [ ] **Step 5: Install healer deps and run tests**

Run: `pip install -e ".[healer]" && python -m pytest tests/test_healer/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add healer/api/ tests/test_healer/test_api.py healer/cli/commands.py pyproject.toml
git commit -m "feat(#4/T10): FastAPI HTTP service + serve CLI command"
```

---

### Task 11: Monitor

**Files:**
- Create: `healer/monitor.py`

- [ ] **Step 1: Implement monitor**

```python
# healer/monitor.py
"""Healer monitor — success rates, recurring patterns, metrics."""
from __future__ import annotations

from collections import Counter
from healer.models import TicketStatus
from healer.queue import HealerQueue


def compute_metrics(queue: HealerQueue) -> dict:
    """Compute aggregate metrics from the queue."""
    all_tickets = queue.list_tickets()

    # Success rate by tier
    tier_success: dict[int, dict[str, int]] = {}
    for t in all_tickets:
        if t.status in (TicketStatus.APPLIED, TicketStatus.FAILED):
            bucket = tier_success.setdefault(t.tier, {"applied": 0, "total": 0})
            bucket["total"] += 1
            if t.status == TicketStatus.APPLIED:
                bucket["applied"] += 1

    success_rates = {}
    for tier, counts in sorted(tier_success.items()):
        rate = counts["applied"] / counts["total"] if counts["total"] > 0 else 0.0
        success_rates[f"tier_{tier}"] = round(rate, 2)

    # Recurring errors
    error_counter: Counter = Counter()
    for t in all_tickets:
        if t.contract_fqn and t.error_type:
            error_counter[(t.contract_fqn, t.error_type)] += 1

    recurring = [
        {"contract_fqn": fqn, "error_type": etype, "count": count}
        for (fqn, etype), count in error_counter.most_common(10)
        if count > 1
    ]

    # Recent tickets
    resolved = [t for t in all_tickets if t.resolved_at]
    resolved.sort(key=lambda t: t.resolved_at or t.created_at, reverse=True)
    recent = [
        {"id": t.id[:8], "fqn": t.contract_fqn, "status": t.status.value, "tier": t.tier}
        for t in resolved[:10]
    ]

    stats = queue.stats()
    return {
        "queue": stats["by_status"],
        "success_rate": success_rates,
        "recurring": recurring,
        "recent": recent,
    }
```

- [ ] **Step 2: Wire monitor into the /healer/status endpoint**

Replace the `queue_status` function in `healer/api/server.py`:

```python
@app.get("/healer/status")
def queue_status():
    from healer.monitor import compute_metrics
    return compute_metrics(get_queue())
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add healer/monitor.py healer/api/server.py
git commit -m "feat(#4/T11): monitor — success rates, recurring patterns, metrics"
```

---

### Task 12: File Watcher + End-to-End Verification

**Files:**
- Create: `healer/watcher.py`

- [ ] **Step 1: Implement file watcher**

```python
# healer/watcher.py
"""File watcher — monitors .forge/healer/inbox/ for error payloads."""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from healer.models import HealerTicket, TicketSource
from healer.queue import HealerQueue

logger = logging.getLogger(__name__)


def process_inbox(
    queue: HealerQueue,
    inbox: Path = Path(".forge/healer/inbox"),
) -> int:
    """Process all JSON files in the inbox directory.

    Moves processed files to inbox/processed/.
    Returns number of files processed.
    """
    if not inbox.exists():
        return 0

    processed_dir = inbox / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for f in sorted(inbox.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            ticket = HealerTicket(
                source=TicketSource(data.get("source", "manual")),
                raw_error=data.get("error", ""),
                contract_fqn=data.get("contract_fqn"),
                context=data.get("context", {}),
            )
            if data.get("stacktrace"):
                ticket.context["stacktrace"] = data["stacktrace"]

            queue.enqueue(ticket)
            shutil.move(str(f), str(processed_dir / f.name))
            count += 1
            logger.info("Processed inbox file: %s -> ticket %s", f.name, ticket.id[:8])
        except Exception as e:
            logger.error("Failed to process %s: %s", f.name, e)

    return count


def watch_loop(
    queue: HealerQueue,
    inbox: Path = Path(".forge/healer/inbox"),
    interval: float = 5.0,
) -> None:
    """Run the file watcher in a loop. Blocks forever."""
    logger.info("Watching %s (interval: %.1fs)", inbox, interval)
    inbox.mkdir(parents=True, exist_ok=True)
    while True:
        process_inbox(queue, inbox)
        time.sleep(interval)
```

- [ ] **Step 2: End-to-end verification**

Run the full test suite:
```bash
python -m pytest tests/ -v --tb=short
```

Validate the todo_list domain is still clean:
```bash
python -m forge.cli.main forge validate domains/todo_list
```

Test the CLI:
```bash
python -m forge.cli.main healer status
python -m forge.cli.main healer --help
```

- [ ] **Step 3: Commit**

```bash
git add healer/watcher.py
git commit -m "feat(#4/T12): file watcher for inbox + end-to-end verification"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `specora forge validate domains/todo_list` — 0 errors
- [ ] `specora forge validate domains/library` — 0 errors
- [ ] `specora healer status` — shows empty queue table
- [ ] `specora healer fix domains/todo_list` — processes (should find 0 errors since contracts are already normalized)
- [ ] `specora healer --help` — shows all subcommands
- [ ] `specora healer serve --help` — shows serve options

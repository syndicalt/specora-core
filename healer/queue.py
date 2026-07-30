# healer/queue.py
"""SQLite-backed priority queue for healer tickets."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from healer.cost import LLMUsage
from healer.models import (
    PRIORITY_ORDER,
    HealerProposal,
    HealerTicket,
    Priority,
    TicketSource,
    TicketStatus,
)

BUSY_TIMEOUT_ENV = "SPECORA_HEALER_DB_BUSY_TIMEOUT_MS"
DEFAULT_BUSY_TIMEOUT_MS = 5_000


class HealerQueue:
    """SQLite-backed priority queue for HealerTickets.

    Stores tickets in a single SQLite database. Priority ordering
    uses a numeric sort key (CRITICAL=0, HIGH=1, MEDIUM=2, LOW=3)
    with FIFO within each priority level.

    Concurrency: the sidecar's HTTP worker threads, the file watcher, and the
    CLI all operate on the same database file. A ``sqlite3.Connection`` is not
    thread-safe, so each thread gets its own; WAL lets the CLI read while the
    sidecar writes, and ``busy_timeout`` makes a concurrent writer wait for the
    lock instead of failing immediately with SQLITE_BUSY.
    """

    def __init__(self, db_path: Path | str = ".forge/healer/healer.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = _busy_timeout_ms()
        self._local = threading.local()
        self._create_tables()

    # -- connection management ------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self._busy_timeout_ms / 1000.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

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
            CREATE INDEX IF NOT EXISTS idx_status_priority
                ON tickets(status, priority_order, created_at);
            CREATE INDEX IF NOT EXISTS idx_contract_fqn ON tickets(contract_fqn);

            CREATE TABLE IF NOT EXISTS consumed_tokens (
                nonce TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                consumed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                model_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                prompt_version TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                ok INTEGER NOT NULL DEFAULT 1,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);
        """)
        self._conn.commit()

    # -- tickets --------------------------------------------------------------

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
        """Peek at the next ticket without claiming it.

        Callers that intend to process the ticket must use :meth:`claim_next`.
        """
        row = self._conn.execute(
            """SELECT * FROM tickets
               WHERE status = 'queued'
               ORDER BY priority_order ASC, created_at ASC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return self._row_to_ticket(row)

    def claim_next(self) -> Optional[HealerTicket]:
        """Atomically take ownership of the highest-priority queued ticket.

        Selecting and then updating in two statements let two workers claim the
        same ticket, which doubled the LLM spend and applied the same fix
        twice. A single UPDATE ... RETURNING makes the claim indivisible.
        """
        row = self._conn.execute(
            """UPDATE tickets
                  SET status = 'analyzing'
                WHERE id = (
                    SELECT id FROM tickets
                     WHERE status = 'queued'
                     ORDER BY priority_order ASC, created_at ASC
                     LIMIT 1
                )
            RETURNING *"""
        ).fetchone()
        self._conn.commit()
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

    # -- single-use approval tokens ------------------------------------------

    def consume_token_nonce(
        self,
        nonce: str,
        ticket_id: str,
        action: str,
        actor: str = "",
    ) -> bool:
        """Burn a signed token's nonce. False when it was already spent.

        The PRIMARY KEY does the work: two concurrent replays of the same link
        cannot both insert, so exactly one of them proceeds.
        """
        try:
            self._conn.execute(
                """INSERT INTO consumed_tokens (nonce, ticket_id, action, actor, consumed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (nonce, ticket_id, action, actor, datetime.now(timezone.utc).isoformat()),
            )
        except sqlite3.IntegrityError:
            # sqlite3 opens the implicit transaction before running the
            # statement, so a constraint violation leaves it open and holding
            # the write lock. Without this rollback one replayed approval link
            # wedges every other writer with SQLITE_BUSY until the process dies.
            self._conn.rollback()
            return False
        self._conn.commit()
        return True

    # -- LLM accounting -------------------------------------------------------

    def record_llm_usage(self, ticket_id: str, usage: LLMUsage) -> None:
        self._conn.execute(
            """INSERT INTO llm_usage
               (ticket_id, model_id, provider, prompt_version, input_tokens,
                output_tokens, latency_ms, cost_usd, ok, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticket_id, usage.model_id, usage.provider, usage.prompt_version,
                usage.input_tokens, usage.output_tokens, usage.latency_ms,
                usage.cost_usd, 1 if usage.ok else 0, usage.error,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def llm_usage_totals_since(self, cutoff: datetime) -> dict:
        row = self._conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
                      COALESCE(AVG(latency_ms), 0.0) AS avg_latency_ms
                 FROM llm_usage
                WHERE created_at >= ?""",
            (cutoff.isoformat(),),
        ).fetchone()
        return {
            "calls": row["calls"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "total_tokens": row["input_tokens"] + row["output_tokens"],
            "total_cost_usd": round(row["total_cost_usd"], 6),
            "avg_latency_ms": round(row["avg_latency_ms"], 1),
        }

    def recent_llm_outcomes(self, limit: int) -> list[tuple[bool, datetime]]:
        rows = self._conn.execute(
            "SELECT ok, created_at FROM llm_usage ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(bool(r["ok"]), datetime.fromisoformat(r["created_at"])) for r in rows]

    # -- internals ------------------------------------------------------------

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
        """Close this thread's connection.

        SQLite forbids touching a connection from a thread other than the one
        that opened it, so a connection belonging to another thread cannot be
        closed here; it is released when that thread exits and its thread-local
        storage is collected.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def _busy_timeout_ms() -> int:
    raw = os.environ.get(BUSY_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_BUSY_TIMEOUT_MS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BUSY_TIMEOUT_MS
    return value if value > 0 else DEFAULT_BUSY_TIMEOUT_MS

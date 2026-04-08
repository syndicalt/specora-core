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
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
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

"""Healer HTTP service — FastAPI endpoints for the self-healing pipeline."""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from healer.models import HealerTicket, Priority, TicketSource, TicketStatus
from healer.monitor import compute_metrics
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue

app = FastAPI(title="Specora Healer", version="0.2.0")

# Module-level globals — set by CLI or tests.
_queue: Optional[HealerQueue] = None
_pipeline: Optional[HealerPipeline] = None


def _get_queue() -> HealerQueue:
    global _queue
    if _queue is None:
        _queue = HealerQueue()
    return _queue


def _get_pipeline() -> HealerPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = HealerPipeline(queue=_get_queue())
    return _pipeline


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    source: str
    contract_fqn: Optional[str] = None
    error: str
    stacktrace: Optional[str] = None
    context: Optional[dict] = None


class IngestResponse(BaseModel):
    ticket_id: str
    status: str


class RejectRequest(BaseModel):
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healer/health")
async def health() -> dict:
    return {"status": "ok", "service": "healer"}


@app.get("/healer/status")
async def status() -> dict:
    return compute_metrics(_get_queue())


@app.post("/healer/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    queue = _get_queue()
    pipeline = _get_pipeline()

    ticket = HealerTicket(
        source=TicketSource(body.source),
        raw_error=body.error,
        contract_fqn=body.contract_fqn,
        context=body.context or {},
    )
    if body.stacktrace:
        ticket.context["stacktrace"] = body.stacktrace

    queue.enqueue(ticket)
    pipeline.process_next()

    # Re-fetch to get updated status
    updated = queue.get_ticket(ticket.id)
    return IngestResponse(
        ticket_id=ticket.id,
        status=updated.status.value if updated else ticket.status.value,
    )


@app.get("/healer/tickets")
async def list_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    contract_fqn: Optional[str] = None,
) -> list[dict]:
    queue = _get_queue()
    status_enum = TicketStatus(status) if status else None
    priority_enum = Priority(priority) if priority else None
    tickets = queue.list_tickets(
        status=status_enum, priority=priority_enum, contract_fqn=contract_fqn,
    )
    return [t.to_dict() for t in tickets]


@app.get("/healer/tickets/{ticket_id}")
async def get_ticket(ticket_id: str) -> dict:
    ticket = _get_queue().get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket.to_dict()


@app.get("/healer/tickets/{ticket_id}/view", response_class=HTMLResponse)
async def view_ticket(ticket_id: str) -> str:
    """HTML ticket detail page with approve/reject buttons."""
    ticket = _get_queue().get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    t = ticket

    # Status badge colors
    status_colors = {
        "queued": "#eab308", "analyzing": "#3b82f6", "proposed": "#06b6d4",
        "approved": "#22c55e", "applied": "#22c55e", "failed": "#ef4444", "rejected": "#ef4444",
    }
    status_color = status_colors.get(t.status.value, "#6b7280")

    priority_colors = {
        "critical": "#ef4444", "high": "#f97316", "medium": "#eab308", "low": "#22c55e",
    }
    priority_color = priority_colors.get(t.priority.value, "#6b7280")

    # Proposal section
    proposal_html = ""
    if t.proposal:
        explanation = t.proposal.explanation.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        changes_html = ""
        for c in t.proposal.changes:
            if hasattr(c, "change_type"):
                changes_html += f'<div style="font-family:monospace;font-size:13px;padding:4px 0;color:#059669">{c.change_type}: {c.path} = {c.new_value}</div>'
            elif isinstance(c, dict):
                changes_html += f'<div style="font-family:monospace;font-size:13px;padding:4px 0;color:#059669">{c.get("change_type","?")}: {c.get("path","?")} = {c.get("new_value","?")}</div>'

        proposal_html = f"""
        <div style="margin-top:24px;padding:20px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px">
            <h3 style="margin:0 0 12px 0;color:#166534">💡 Proposed Fix</h3>
            <div style="margin-bottom:12px">{explanation}</div>
            <div style="background:#fff;padding:12px;border-radius:6px;border:1px solid #d1d5db">
                <strong>Changes:</strong>
                {changes_html or '<div style="color:#6b7280">No structural changes</div>'}
            </div>
            <div style="margin-top:8px;color:#6b7280;font-size:13px">
                Confidence: {t.proposal.confidence} | Method: {t.proposal.method}
            </div>
        </div>"""

    # Action buttons (only for proposed status)
    actions_html = ""
    if t.status.value == "proposed":
        actions_html = f"""
        <div style="margin-top:24px;display:flex;gap:12px">
            <form method="post" action="/healer/approve/{ticket_id}/action" style="margin:0">
                <button type="submit" style="background:#22c55e;color:white;border:none;padding:12px 32px;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer">
                    ✅ Approve Fix
                </button>
            </form>
            <form method="post" action="/healer/reject/{ticket_id}/action" style="margin:0">
                <button type="submit" style="background:#ef4444;color:white;border:none;padding:12px 32px;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer">
                    🚫 Reject
                </button>
            </form>
        </div>"""

    # Resolution note
    resolution_html = ""
    if t.resolution_note:
        resolution_html = f"""
        <div style="margin-top:16px;padding:12px;background:#f3f4f6;border-radius:6px;color:#374151">
            <strong>Resolution:</strong> {t.resolution_note}
        </div>"""

    error_text = t.raw_error.replace("<", "&lt;").replace(">", "&gt;")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Healer Ticket {ticket_id[:8]}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; background: #f9fafb; color: #111827; }}
        .container {{ max-width: 720px; margin: 0 auto; padding: 32px 24px; }}
        .header {{ display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }}
        .badge {{ display: inline-block; padding: 4px 12px; border-radius: 9999px; font-size: 13px; font-weight: 600; color: white; }}
        .meta {{ color: #6b7280; font-size: 13px; margin-bottom: 24px; }}
        .field {{ margin-bottom: 16px; }}
        .field-label {{ font-size: 12px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }}
        .field-value {{ font-size: 15px; }}
        .error-box {{ background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 16px; margin-top: 16px; }}
        .logo {{ font-size: 13px; color: #9ca3af; margin-top: 32px; padding-top: 16px; border-top: 1px solid #e5e7eb; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0;font-size:24px">Healer Ticket</h1>
            <span class="badge" style="background:{status_color}">{t.status.value.upper()}</span>
            <span class="badge" style="background:{priority_color}">{t.priority.value}</span>
        </div>
        <div class="meta">{ticket_id} &middot; Tier {t.tier} &middot; Source: {t.source.value}</div>

        <div class="field">
            <div class="field-label">Contract</div>
            <div class="field-value"><code>{t.contract_fqn or 'unknown'}</code></div>
        </div>

        <div class="field">
            <div class="field-label">Error</div>
            <div class="error-box">{error_text}</div>
        </div>

        {proposal_html}
        {actions_html}
        {resolution_html}

        <div class="logo">Specora Healer &middot; Self-healing software</div>
    </div>
</body>
</html>"""
    return html


@app.post("/healer/approve/{ticket_id}/action", response_class=HTMLResponse)
async def approve_action(ticket_id: str) -> str:
    """HTML form action — approve and redirect back to view."""
    pipeline = _get_pipeline()
    success = pipeline.approve_ticket(ticket_id)
    if success:
        return f"""<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url=/healer/tickets/{ticket_id}/view"></head>
        <body>Approved. Redirecting...</body></html>"""
    return f"""<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url=/healer/tickets/{ticket_id}/view"></head>
    <body>Could not approve. Redirecting...</body></html>"""


@app.post("/healer/reject/{ticket_id}/action", response_class=HTMLResponse)
async def reject_action(ticket_id: str) -> str:
    """HTML form action — reject and redirect back to view."""
    pipeline = _get_pipeline()
    pipeline.reject_ticket(ticket_id, reason="Rejected via web UI")
    return f"""<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url=/healer/tickets/{ticket_id}/view"></head>
    <body>Rejected. Redirecting...</body></html>"""


@app.post("/healer/approve/{ticket_id}")
async def approve(ticket_id: str) -> dict:
    pipeline = _get_pipeline()
    success = pipeline.approve_ticket(ticket_id)
    if not success:
        ticket = _get_queue().get_ticket(ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve: ticket is '{ticket.status.value}' (must be 'proposed')",
        )
    return {"ticket_id": ticket_id, "status": "approved"}


@app.post("/healer/reject/{ticket_id}")
async def reject(ticket_id: str, body: Optional[RejectRequest] = None) -> dict:
    pipeline = _get_pipeline()
    reason = body.reason if body and body.reason else ""
    success = pipeline.reject_ticket(ticket_id, reason=reason)
    if not success:
        ticket = _get_queue().get_ticket(ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject: ticket is '{ticket.status.value}' (must be 'proposed')",
        )
    return {"ticket_id": ticket_id, "status": "rejected"}

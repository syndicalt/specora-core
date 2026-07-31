"""Healer HTTP service — FastAPI endpoints for the self-healing pipeline.

The surface is split into two planes with different exposure and different
credentials. See :mod:`healer.security` for the full design and rationale.

  Data plane (internal only)  /healer/health, /healer/status, /healer/ingest
  Control plane (public)      /healer/tickets*, /healer/approve*, /healer/reject*

Ingest never runs the pipeline inline. It enqueues, returns 202, and wakes a
background worker that drains the queue on a thread. Running the pipeline —
LLM round trips, file I/O, a full regeneration — inside the request coroutine
blocked the event loop, so an error storm (which is exactly when the generated
app reports hardest) serialised every request behind it and the health check
timed out, which made Docker restart the Healer.
"""
from __future__ import annotations

import asyncio
import html
import logging
from contextlib import asynccontextmanager, suppress
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from healer.models import HealerTicket, Priority, TicketSource, TicketStatus
from healer.monitor import compute_metrics
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue
from healer.ratelimit import TokenBucketLimiter
from healer.security import (
    ACTION_APPROVE,
    ACTION_LIST,
    ACTION_REJECT,
    ACTION_VIEW,
    MUTATING_ACTIONS,
    Actor,
    AuthError,
    Credentials,
    approval_tokens_enabled,
    authorize_control_plane,
    issue_action_token,
    issue_csrf_token,
    require_ingest_token,
    verify_csrf_token,
)

logger = logging.getLogger(__name__)

# How long the worker sleeps before re-checking the queue when nothing has
# signalled it. Tickets can also arrive from the CLI or the file watcher
# writing to the same database, which produce no in-process signal.
WORKER_POLL_SECONDS = 5.0

# Actor kinds whose credential a browser may attach automatically (an identity
# proxy's cookie), and which therefore need CSRF protection on mutations.
_AMBIENT_ACTOR_KINDS = frozenset({"proxy"})

# Module-level globals — set by CLI or tests.
_queue: Optional[HealerQueue] = None
_pipeline: Optional[HealerPipeline] = None
_limiter: Optional[TokenBucketLimiter] = None
_work_signal: Optional[asyncio.Event] = None


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


def _get_limiter() -> TokenBucketLimiter:
    global _limiter
    if _limiter is None:
        _limiter = TokenBucketLimiter()
    return _limiter


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

async def _drain_worker() -> None:
    while True:
        if _work_signal is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(_work_signal.wait(), timeout=WORKER_POLL_SECONDS)
            _work_signal.clear()
        else:
            await asyncio.sleep(WORKER_POLL_SECONDS)
        try:
            # to_thread keeps the synchronous pipeline off the event loop.
            while await asyncio.to_thread(_get_pipeline().process_next):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Healer worker failed while draining the queue")
            await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _work_signal
    _work_signal = asyncio.Event()
    worker = asyncio.create_task(_drain_worker())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        _work_signal = None


app = FastAPI(title="Specora Healer", version="0.2.0", lifespan=lifespan)


@app.exception_handler(AuthError)
async def _auth_error_handler(_request: Request, exc: AuthError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}, headers=headers
    )


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
# Credential plumbing
# ---------------------------------------------------------------------------

def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _credentials(request: Request, form: Optional[dict] = None) -> Credentials:
    return Credentials(
        bearer=_bearer(request),
        query_token=request.query_params.get("t", ""),
        form_token=(form or {}).get("token", ""),
        headers=request.headers,
    )


def _is_form_post(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    return content_type.startswith("application/x-www-form-urlencoded") or (
        content_type.startswith("multipart/form-data")
    )


async def _parse_form(request: Request) -> dict[str, str]:
    """Read an urlencoded form body.

    Starlette's ``request.form()`` pulls in python-multipart even for
    urlencoded bodies. The Healer only ever posts its own two-field form, so
    parsing it directly keeps the sidecar's dependency set smaller. A multipart
    body yields an empty mapping, which makes the CSRF check fail closed.
    """
    if not request.headers.get("content-type", "").startswith(
        "application/x-www-form-urlencoded"
    ):
        return {}
    body = (await request.body()).decode("utf-8", errors="replace")
    return dict(parse_qsl(body, keep_blank_values=True))


async def _authorize(
    request: Request,
    ticket_id: str,
    action: str,
    form: Optional[dict] = None,
) -> Actor:
    """Authenticate, enforce CSRF, and burn single-use tokens.

    Every control-plane handler goes through here; there is no path that
    reaches a mutation without it.
    """
    actor = authorize_control_plane(_credentials(request, form), ticket_id, action)

    if action in MUTATING_ACTIONS:
        needs_csrf = actor.kind in _AMBIENT_ACTOR_KINDS or _is_form_post(request)
        if needs_csrf:
            supplied = (form or {}).get("csrf", "") or request.headers.get(
                "x-specora-csrf", ""
            )
            verify_csrf_token(supplied, ticket_id, action, actor)

        if actor.nonce and not _get_queue().consume_token_nonce(
            actor.nonce, ticket_id, action, actor.audit_id
        ):
            raise AuthError(401, "This approval link has already been used.")

    return actor


# ---------------------------------------------------------------------------
# Data plane — internal only, shared-secret authenticated
# ---------------------------------------------------------------------------

@app.get("/healer/health")
async def health() -> dict:
    """Liveness only. Unauthenticated so orchestrator probes need no secret."""
    return {"status": "ok", "service": "healer"}


@app.get("/healer/status")
async def status(request: Request) -> dict:
    require_ingest_token(_credentials(request))
    return compute_metrics(_get_queue())


@app.post("/healer/ingest", response_model=IngestResponse, status_code=202)
async def ingest(body: IngestRequest, request: Request) -> IngestResponse:
    require_ingest_token(_credentials(request))

    decision = _get_limiter().check(body.contract_fqn)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Ingest rate limit exceeded for {decision.scope}.",
            headers={"Retry-After": str(max(1, int(decision.retry_after_seconds)))},
        )

    queue = _get_queue()
    ticket = HealerTicket(
        source=TicketSource(body.source),
        raw_error=body.error,
        contract_fqn=body.contract_fqn,
        context=body.context or {},
    )
    if body.stacktrace:
        ticket.context["stacktrace"] = body.stacktrace

    queue.enqueue(ticket)
    if _work_signal is not None:
        _work_signal.set()

    return IngestResponse(ticket_id=ticket.id, status=ticket.status.value)


# ---------------------------------------------------------------------------
# Control plane — public, authenticated to a principal
# ---------------------------------------------------------------------------

@app.get("/healer/tickets")
async def list_tickets(
    request: Request,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    contract_fqn: Optional[str] = None,
) -> list[dict]:
    await _authorize(request, "", ACTION_LIST)
    queue = _get_queue()
    status_enum = TicketStatus(status) if status else None
    priority_enum = Priority(priority) if priority else None
    tickets = queue.list_tickets(
        status=status_enum, priority=priority_enum, contract_fqn=contract_fqn,
    )
    return [t.to_dict() for t in tickets]


@app.get("/healer/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, request: Request) -> dict:
    await _authorize(request, ticket_id, ACTION_VIEW)
    ticket = _get_queue().get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket.to_dict()


@app.get("/healer/tickets/{ticket_id}/view", response_class=HTMLResponse)
async def view_ticket(ticket_id: str, request: Request) -> str:
    """HTML ticket detail page with approve/reject buttons."""
    actor = await _authorize(request, ticket_id, ACTION_VIEW)
    ticket = _get_queue().get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _render_ticket_page(ticket, ticket_id, actor)


@app.post("/healer/approve/{ticket_id}/action", response_class=HTMLResponse)
async def approve_action(ticket_id: str, request: Request) -> str:
    """HTML form action — approve and redirect back to view."""
    form = await _parse_form(request)
    actor = await _authorize(request, ticket_id, ACTION_APPROVE, form)
    success = _get_pipeline().approve_ticket(ticket_id, actor=actor.audit_id)
    message = "Approved." if success else "Could not approve."
    return _redirect_page(ticket_id, message, form.get("token", ""))


@app.post("/healer/reject/{ticket_id}/action", response_class=HTMLResponse)
async def reject_action(ticket_id: str, request: Request) -> str:
    """HTML form action — reject and redirect back to view."""
    form = await _parse_form(request)
    actor = await _authorize(request, ticket_id, ACTION_REJECT, form)
    _get_pipeline().reject_ticket(
        ticket_id, reason="Rejected via web UI", actor=actor.audit_id
    )
    return _redirect_page(ticket_id, "Rejected.", form.get("token", ""))


@app.post("/healer/approve/{ticket_id}")
async def approve(ticket_id: str, request: Request) -> dict:
    actor = await _authorize(request, ticket_id, ACTION_APPROVE)
    success = _get_pipeline().approve_ticket(ticket_id, actor=actor.audit_id)
    if not success:
        ticket = _get_queue().get_ticket(ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve: ticket is '{ticket.status.value}' (must be 'proposed')",
        )
    return {"ticket_id": ticket_id, "status": "approved", "actor": actor.audit_id}


@app.post("/healer/reject/{ticket_id}")
async def reject(
    ticket_id: str, request: Request, body: Optional[RejectRequest] = None
) -> dict:
    actor = await _authorize(request, ticket_id, ACTION_REJECT)
    reason = body.reason if body and body.reason else ""
    success = _get_pipeline().reject_ticket(ticket_id, reason=reason, actor=actor.audit_id)
    if not success:
        ticket = _get_queue().get_ticket(ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject: ticket is '{ticket.status.value}' (must be 'proposed')",
        )
    return {"ticket_id": ticket_id, "status": "rejected", "actor": actor.audit_id}


# ---------------------------------------------------------------------------
# HTML rendering
#
# Every interpolated value goes through _esc. Ticket fields — contract_fqn,
# raw_error, context — arrive over the wire and are attacker-controlled, and
# the page they land on is read by the one person authorised to approve a
# contract rewrite. Hand-rolled escaping missed &, " and ', and several values
# were interpolated with none at all.
# ---------------------------------------------------------------------------

def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _esc_multiline(value: object) -> str:
    return _esc(value).replace("\n", "<br>")


def _hidden_credentials(ticket_id: str, action: str, actor: Actor) -> str:
    """Hidden inputs carrying the action credential and the CSRF token.

    The action token is minted here rather than carried from the notification
    because the actor has already proved possession of a valid credential for
    this ticket; this is the same trade a session cookie makes, with a much
    shorter lifetime and single use. It is omitted when signed tokens are not
    configured, in which case the POST re-presents whatever credential
    authenticated the GET.
    """
    csrf = _esc(issue_csrf_token(ticket_id, action, actor))
    fields = [f'<input type="hidden" name="csrf" value="{csrf}">']
    if approval_tokens_enabled():
        token = issue_action_token(
            ticket_id, action, ttl_seconds=3600, subject=actor.principal
        )
        fields.append(f'<input type="hidden" name="token" value="{_esc(token)}">')
    return "".join(fields)


_PAGE_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       margin: 0; background: #f9fafb; color: #111827; }
.container { max-width: 720px; margin: 0 auto; padding: 32px 24px; }
.header { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 9999px;
         font-size: 13px; font-weight: 600; color: white; }
.meta { color: #6b7280; font-size: 13px; margin-bottom: 24px; }
.field { margin-bottom: 16px; }
.field-label { font-size: 12px; font-weight: 600; color: #6b7280;
               text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.field-value { font-size: 15px; }
.error-box { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px;
             padding: 16px; margin-top: 16px; white-space: pre-wrap; }
.logo { font-size: 13px; color: #9ca3af; margin-top: 32px; padding-top: 16px;
        border-top: 1px solid #e5e7eb; }
.action { color: white; border: none; padding: 12px 32px; border-radius: 8px;
          font-size: 16px; font-weight: 600; cursor: pointer; }
.proposal { margin-top: 24px; padding: 20px; background: #f0fdf4;
            border: 1px solid #bbf7d0; border-radius: 8px; }
.change { font-family: monospace; font-size: 13px; padding: 4px 0; color: #059669; }
.resolution { margin-top: 16px; padding: 12px; background: #f3f4f6;
              border-radius: 6px; color: #374151; }
"""


def _render_ticket_page(t: HealerTicket, ticket_id: str, actor: Actor) -> str:
    status_colors = {
        "queued": "#eab308", "analyzing": "#3b82f6", "proposed": "#06b6d4",
        "approved": "#22c55e", "applied": "#22c55e",
        "failed": "#ef4444", "rejected": "#ef4444",
    }
    status_color = status_colors.get(t.status.value, "#6b7280")

    priority_colors = {
        "critical": "#ef4444", "high": "#f97316", "medium": "#eab308", "low": "#22c55e",
    }
    priority_color = priority_colors.get(t.priority.value, "#6b7280")

    proposal_html = ""
    if t.proposal:
        explanation = _esc_multiline(t.proposal.explanation)
        changes_html = ""
        for c in t.proposal.changes:
            if hasattr(c, "change_type"):
                change_type, path, new_value = c.change_type, c.path, c.new_value
            elif isinstance(c, dict):
                change_type = c.get("change_type", "?")
                path = c.get("path", "?")
                new_value = c.get("new_value", "?")
            else:
                continue
            changes_html += (
                f'<div class="change">{_esc(change_type)}: '
                f"{_esc(path)} = {_esc(new_value)}</div>"
            )

        provenance = t.proposal.provenance
        provenance_bits = [
            f"Confidence: {_esc(t.proposal.confidence)}",
            f"Method: {_esc(t.proposal.method)}",
        ]
        if provenance.model_id:
            provenance_bits.append(f"Model: {_esc(provenance.model_id)}")
        if provenance.prompt_version:
            provenance_bits.append(f"Prompt: {_esc(provenance.prompt_version)}")
        if provenance.usage:
            provenance_bits.append(
                f"Tokens: {_esc(provenance.usage.total_tokens)} "
                f"({_esc(provenance.usage.latency_ms)} ms)"
            )

        proposal_html = f"""
        <div class="proposal">
            <h3 style="margin:0 0 12px 0;color:#166534">💡 Proposed Fix</h3>
            <div style="margin-bottom:12px">{explanation}</div>
            <div style="background:#fff;padding:12px;border-radius:6px;border:1px solid #d1d5db">
                <strong>Changes:</strong>
                {changes_html or '<div style="color:#6b7280">No structural changes</div>'}
            </div>
            <div style="margin-top:8px;color:#6b7280;font-size:13px">
                {" | ".join(provenance_bits)}
            </div>
        </div>"""

    actions_html = ""
    if t.status.value == "proposed":
        safe_id = _esc(ticket_id)
        actions_html = f"""
        <div style="margin-top:24px;display:flex;gap:12px">
            <form method="post" action="/healer/approve/{safe_id}/action" style="margin:0">
                {_hidden_credentials(ticket_id, ACTION_APPROVE, actor)}
                <button type="submit" class="action" style="background:#22c55e">
                    ✅ Approve Fix
                </button>
            </form>
            <form method="post" action="/healer/reject/{safe_id}/action" style="margin:0">
                {_hidden_credentials(ticket_id, ACTION_REJECT, actor)}
                <button type="submit" class="action" style="background:#ef4444">
                    🚫 Reject
                </button>
            </form>
        </div>"""

    resolution_html = ""
    if t.resolution_note:
        resolution_html = f"""
        <div class="resolution">
            <strong>Resolution:</strong> {_esc(t.resolution_note)}
        </div>"""

    meta_line = (
        f"{_esc(ticket_id)} &middot; Tier {_esc(t.tier)} "
        f"&middot; Source: {_esc(t.source.value)}"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="referrer" content="no-referrer">
    <title>Healer Ticket {_esc(ticket_id[:8])}</title>
    <style>{_PAGE_CSS}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0;font-size:24px">Healer Ticket</h1>
            <span class="badge" style="background:{status_color}">
                {_esc(t.status.value.upper())}</span>
            <span class="badge" style="background:{priority_color}">
                {_esc(t.priority.value)}</span>
        </div>
        <div class="meta">{meta_line}</div>

        <div class="field">
            <div class="field-label">Contract</div>
            <div class="field-value"><code>{_esc(t.contract_fqn or 'unknown')}</code></div>
        </div>

        <div class="field">
            <div class="field-label">Error</div>
            <div class="error-box">{_esc(t.raw_error)}</div>
        </div>

        {proposal_html}
        {actions_html}
        {resolution_html}

        <div class="logo">Specora Healer &middot; Self-healing software
            &middot; signed in as {_esc(actor.audit_id)}</div>
    </div>
</body>
</html>"""


def _redirect_page(ticket_id: str, message: str, view_token: str) -> str:
    """Confirmation page linking back to the ticket.

    A meta-refresh would drop the credential, and re-minting a view token here
    would hand out a fresh credential on an unauthenticated render path, so the
    outcome is stated and the link is only offered when the caller already
    holds a token.
    """
    safe_id = _esc(ticket_id)
    link = ""
    if view_token and approval_tokens_enabled():
        token = issue_action_token(ticket_id, ACTION_VIEW, ttl_seconds=3600)
        link = f'<p><a href="/healer/tickets/{safe_id}/view?t={_esc(token)}">Back to ticket</a></p>'
    return (
        "<!DOCTYPE html><html><head><meta charset=\"UTF-8\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>Healer</title></head><body><p>{_esc(message)}</p>{link}</body></html>"
    )

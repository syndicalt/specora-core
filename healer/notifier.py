"""Notification channels — console, webhook, file.

Webhook auto-detects format from URL:
  - Discord (discord.com/api/webhooks) → {"content": "..."}
  - Slack (hooks.slack.com) → {"text": "..."}
  - Teams (webhook.office.com) → {"text": "..."}
  - Everything else → raw JSON payload
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from rich.console import Console

from healer.models import HealerTicket
from healer.security import (
    ACTION_VIEW,
    AuthError,
    approval_tokens_enabled,
    issue_action_token,
    public_base_url,
)

logger = logging.getLogger(__name__)
console = Console()

ICONS = {
    "queued": "📥", "applied": "✅", "failed": "❌",
    "proposed": "💡", "rejected": "🚫", "approved": "👍",
}


class Notifier:

    def __init__(
        self,
        log_path: Path = Path(".forge/healer/notifications.jsonl"),
        webhook_url: Optional[str] = None,
    ) -> None:
        self.log_path = Path(log_path)
        raw = webhook_url or os.environ.get("SPECORA_HEALER_WEBHOOK_URL", "")
        self.webhook_urls = [u.strip() for u in raw.split(",") if u.strip()]

    def notify(
        self,
        ticket: HealerTicket,
        event: str,
        message: str = "",
    ) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "ticket_id": ticket.id,
            "contract_fqn": ticket.contract_fqn,
            "status": ticket.status.value,
            "tier": ticket.tier,
            "priority": ticket.priority.value,
            "message": message or ticket.raw_error[:500],
        }

        self._log_to_file(payload)
        self._log_to_console(payload)
        for url in self.webhook_urls:
            self._send_webhook(payload, url)

    def _log_to_file(self, payload: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")

    def _log_to_console(self, payload: dict) -> None:
        event = payload["event"]
        fqn = payload.get("contract_fqn") or "unknown"
        colors = {
            "queued": "yellow", "applied": "green", "failed": "red",
            "proposed": "cyan", "rejected": "red",
        }
        color = colors.get(event, "white")
        message = payload.get("message", "")[:80]
        console.print(f"[{color}][healer/{event}][/{color}] {fqn}: {message}")

    def _send_webhook(self, payload: dict, url: str = "") -> None:
        try:
            import httpx

            text = self._format_message(payload)

            if "discord.com/api/webhooks" in url:
                body = {"content": text}
            elif "hooks.slack.com" in url:
                body = {"text": text}
            elif "webhook.office.com" in url or "outlook.office.com" in url:
                body = {"text": text}
            else:
                # Raw JSON for generic webhooks / API gateways
                body = payload

            httpx.post(url, json=body, timeout=5.0)
        except Exception as e:
            logger.warning("Webhook failed: %s", e)

    def _format_message(self, payload: dict) -> str:
        event = payload.get("event", "unknown")
        fqn = payload.get("contract_fqn") or "unknown"
        msg = payload.get("message", "")
        tier = payload.get("tier", "?")
        priority = payload.get("priority", "?")
        ticket_id = payload.get("ticket_id", "")
        icon = ICONS.get(event, "🔔")

        ticket_url = _ticket_url(ticket_id)

        lines = [
            f"{icon} **Specora Healer — {event.upper()}**",
            "",
            f"**Contract:** `{fqn}`",
            f"**Priority:** {priority} | **Tier:** {tier}",
            "",
        ]

        # Truncate message cleanly at word boundary
        if len(msg) > 800:
            msg = msg[:800].rsplit(" ", 1)[0] + "…"
        lines.append(msg)

        if ticket_url:
            lines.append("")
            lines.append(f"🔗 [View ticket]({ticket_url})")

        return "\n".join(lines)


def _ticket_url(ticket_id: str) -> str:
    """Link to the ticket, carrying a signed view token when one can be minted.

    The token is a bearer credential, so this URL is only as private as the
    channel it is posted to — which is the point of the design: the webhook
    already goes to a private Slack/Discord/Teams channel, and embedding the
    credential is what removes the need for a login page on the Healer.
    """
    if not ticket_id:
        return ""
    base = public_base_url()
    url = f"{base}/healer/tickets/{ticket_id}/view"
    if not approval_tokens_enabled():
        return url
    try:
        token = issue_action_token(ticket_id, ACTION_VIEW)
    except AuthError as exc:
        logger.warning("Could not mint a view token: %s", exc.detail)
        return url
    return f"{url}?t={quote(token, safe='')}"

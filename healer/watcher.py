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
    logger.info("Watching %s (interval: %.1fs)", inbox, interval)
    inbox.mkdir(parents=True, exist_ok=True)
    while True:
        process_inbox(queue, inbox)
        time.sleep(interval)

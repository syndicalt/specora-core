"""Healer monitor — success rates, recurring patterns, metrics."""
from __future__ import annotations

from collections import Counter
from healer.models import TicketStatus
from healer.queue import HealerQueue


def compute_metrics(queue: HealerQueue) -> dict:
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

    # Recent resolved tickets
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

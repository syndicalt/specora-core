"""Healer data models — tickets, proposals, and enums for the healing pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from healer.cost import LLMUsage


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


PRIORITY_ORDER = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.MEDIUM: 2,
    Priority.LOW: 3,
}


@dataclass
class ProposalProvenance:
    """Where a proposal came from, in enough detail to replay or blame it.

    A bad fix that reached a contract must be attributable: which model, which
    prompt revision, and what the call cost. Without this a regression in a
    provider's model is indistinguishable from a regression in our prompt.
    """

    proposer: str = ""
    proposer_version: str = ""
    model_id: str = ""
    provider: str = ""
    prompt_version: str = ""
    usage: Optional[LLMUsage] = None
    attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "proposer": self.proposer,
            "proposer_version": self.proposer_version,
            "model_id": self.model_id,
            "provider": self.provider,
            "prompt_version": self.prompt_version,
            "usage": self.usage.to_dict() if self.usage else None,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProposalProvenance:
        usage = d.get("usage")
        return cls(
            proposer=d.get("proposer", ""),
            proposer_version=d.get("proposer_version", ""),
            model_id=d.get("model_id", ""),
            provider=d.get("provider", ""),
            prompt_version=d.get("prompt_version", ""),
            usage=LLMUsage.from_dict(usage) if usage else None,
            attempts=int(d.get("attempts", 0)),
        )


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
        provenance: Optional[ProposalProvenance] = None,
    ) -> None:
        self.contract_fqn = contract_fqn
        self.before = before
        self.after = after
        self.changes = changes
        self.explanation = explanation
        self.confidence = confidence
        self.method = method
        self.provenance = provenance or ProposalProvenance()

    def to_dict(self) -> dict:
        return {
            "contract_fqn": self.contract_fqn,
            "before": self.before,
            "after": self.after,
            "changes": [
                c.model_dump(mode="json") if hasattr(c, "model_dump") else c for c in self.changes
            ],
            "explanation": self.explanation,
            "confidence": self.confidence,
            "method": self.method,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> HealerProposal:
        provenance = d.get("provenance")
        return cls(
            contract_fqn=d["contract_fqn"],
            before=d["before"],
            after=d["after"],
            changes=d.get("changes", []),
            explanation=d["explanation"],
            confidence=d["confidence"],
            method=d["method"],
            provenance=ProposalProvenance.from_dict(provenance) if provenance else None,
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

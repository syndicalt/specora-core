"""Contract diff models — the data structures that track every contract mutation.

Every time a contract is modified — by a human, the Healer, the Advisor,
or the Factory — a ContractDiff is created. Diffs record:

  - WHAT changed (field-level changes with JSONPath-like paths)
  - WHO changed it (origin: human, healer, advisor, factory)
  - WHY it changed (reason: human-readable explanation)
  - WHEN it changed (timestamp)
  - Full before/after snapshots (for LLM context)

This is NOT a replacement for git. Git tracks file-level changes.
The diff system tracks semantic, field-level changes within contracts
and provides queryable context for the Healer and Advisor.

Usage:
    from forge.diff.models import ContractDiff, FieldChange, DiffOrigin

    diff = ContractDiff(
        contract_fqn="entity/itsm/incident",
        origin=DiffOrigin.HEALER,
        origin_detail="healer:bug-1234",
        reason="Added deleted_reference_handling rule to prevent null name display",
        changes=[
            FieldChange(
                path="spec.fields.assigned_to.references",
                old_value={"entity": "entity/itsm/user", "display": "name"},
                new_value={"entity": "entity/itsm/user", "display": "name", "on_deleted": "[deleted]"},
                change_type="modified",
            )
        ],
        before_snapshot={...},
        after_snapshot={...},
    )
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DiffOrigin(str, Enum):
    """Who or what proposed this contract change.

    Every diff has an origin so you can trace the evolution of contracts:
      - HUMAN:   A developer edited the contract directly
      - HEALER:  The self-healing system detected a bug and proposed a fix
      - ADVISOR: The advisory system observed a pattern and proposed an evolution
      - FACTORY: The conversational CLI authored or modified the contract
    """

    HUMAN = "human"
    HEALER = "healer"
    ADVISOR = "advisor"
    FACTORY = "factory"


class FieldChange(BaseModel):
    """A single field-level change within a contract.

    Uses JSONPath-like dot notation to identify the location:
      "spec.fields.priority.type"          — a field type changed
      "spec.fields.assigned_to.references" — a reference annotation changed
      "spec.transitions.new"               — a workflow transition changed
      "metadata.description"               — the description changed

    Attributes:
        path: Dot-notation path to the changed field within the contract.
        old_value: The value before the change. None for additions.
        new_value: The value after the change. None for removals.
        change_type: The nature of the change.
            - "added":        Field was added (old_value is None)
            - "removed":      Field was removed (new_value is None)
            - "modified":     Field value changed
            - "type_changed": Field type changed (a special case of modified)
    """

    path: str
    old_value: Any = None
    new_value: Any = None
    change_type: str = Field(
        ...,
        pattern="^(added|removed|modified|type_changed)$",
        description="One of: added, removed, modified, type_changed",
    )


class ContractDiff(BaseModel):
    """A complete diff record for a single contract mutation.

    This is the primary artifact of the diff tracking system. Every
    contract change — no matter how small — produces a ContractDiff.

    The before_snapshot and after_snapshot fields store the full contract
    content. This is intentionally redundant with git: the diff system
    exists so the Healer and Advisor can query contract evolution without
    parsing git history. LLMs get rich context about how and why a
    contract evolved.

    Attributes:
        id: Unique identifier for this diff (UUID).
        contract_fqn: Fully Qualified Name of the contract that changed.
            Format: "kind/domain/name" (e.g., "entity/itsm/incident").
        timestamp: When the change was recorded (UTC).
        origin: Who or what proposed this change.
        origin_detail: Specific identifier for the change source.
            Examples: "human:jdoe", "healer:bug-1234", "advisor:perf-opt-7"
        reason: Human-readable explanation of WHY this change was made.
            This is the most important field for LLM context — it tells
            the Healer/Advisor the intent behind previous changes.
        changes: List of field-level changes within the contract.
        before_hash: SHA-256 hash of the contract before the change.
        after_hash: SHA-256 hash of the contract after the change.
        before_snapshot: Complete contract content before the change.
        after_snapshot: Complete contract content after the change.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contract_fqn: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    origin: DiffOrigin
    origin_detail: str = ""
    reason: str
    changes: list[FieldChange]
    before_hash: str
    after_hash: str
    before_snapshot: dict
    after_snapshot: dict


def hash_contract(contract: dict) -> str:
    """Compute a deterministic SHA-256 hash of a contract.

    The contract dict is serialized to JSON with sorted keys and no
    extra whitespace. This ensures the same contract content always
    produces the same hash, regardless of YAML key ordering or
    formatting differences.

    Args:
        contract: The contract dict to hash.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

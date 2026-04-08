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
    success: bool
    error: str = ""


def apply_fix(
    proposal: HealerProposal,
    contract_path: Path,
    diff_root: Path = Path(".forge/diffs"),
    ticket_id: str = "",
) -> ApplyResult:
    original_content = contract_path.read_text(encoding="utf-8")

    new_content = yaml.dump(
        proposal.after, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    contract_path.write_text(new_content, encoding="utf-8")

    errors = validate_contract(proposal.after)
    real_errors = [e for e in errors if e.severity == "error"]

    if real_errors:
        contract_path.write_text(original_content, encoding="utf-8")
        error_msgs = "; ".join(e.message for e in real_errors[:3])
        logger.warning("Fix failed validation, rolling back: %s", error_msgs)
        return ApplyResult(success=False, error=error_msgs)

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

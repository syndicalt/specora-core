"""Tier 1 proposer — deterministic fixes via normalize_contract()."""
from __future__ import annotations

import copy
from typing import Optional

from forge.diff.tracker import compute_diff
from forge.normalize import normalize_contract
from healer.models import HealerProposal


def propose_deterministic_fix(
    contract_fqn: str,
    contract: dict,
) -> Optional[HealerProposal]:
    before = copy.deepcopy(contract)
    after = copy.deepcopy(contract)
    normalize_contract(after)

    changes = compute_diff(before, after)
    if not changes:
        return None

    change_descriptions = []
    for c in changes:
        if c.change_type == "modified":
            change_descriptions.append(f"{c.path}: {c.old_value!r} → {c.new_value!r}")

    explanation = "Deterministic normalization: " + "; ".join(change_descriptions[:5])
    if len(change_descriptions) > 5:
        explanation += f" (and {len(change_descriptions) - 5} more)"

    return HealerProposal(
        contract_fqn=contract_fqn,
        before=before,
        after=after,
        changes=changes,
        explanation=explanation,
        confidence=1.0,
        method="deterministic",
    )

"""Tier 1 proposer — deterministic fixes via normalize_contract()."""
from __future__ import annotations

import copy
from typing import Optional

from forge.diff.tracker import compute_diff
from forge.normalize import normalize_contract
from healer.applier import strip_internal_keys
from healer.models import HealerProposal, ProposalProvenance

# Bumped whenever normalize_contract's behaviour changes in a way that alters
# what this proposer emits, so a fix can be traced to the logic that produced it.
NORMALIZER_VERSION = "1"

# No model is consulted: the output is a pure function of the input contract
# and is fully reproducible, so there is no sampling uncertainty to discount.
# This is the one proposer allowed to clear an auto-apply threshold.
DETERMINISTIC_CONFIDENCE = 1.0


def propose_deterministic_fix(
    contract_fqn: str,
    contract: dict,
) -> Optional[HealerProposal]:
    # ``before`` stays verbatim so that a file already contaminated with
    # loader bookkeeping (``_source_path``) produces a real diff and gets
    # repaired; stripping both sides would make the decontamination invisible
    # and the proposer would conclude there was nothing to fix.
    before = copy.deepcopy(contract)
    after = strip_internal_keys(contract)
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
        confidence=DETERMINISTIC_CONFIDENCE,
        method="deterministic",
        provenance=ProposalProvenance(
            proposer="normalize_contract",
            proposer_version=NORMALIZER_VERSION,
            attempts=1,
        ),
    )

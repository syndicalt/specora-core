"""Tier 2-3 proposer — LLM-powered structural and runtime fixes."""
from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from forge.diff.store import DiffStore
from forge.diff.tracker import compute_diff
from forge.parser.validator import validate_contract
from healer.models import HealerProposal, HealerTicket

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a contract healing expert. Fix the contract YAML to resolve the error.

STRICT RULES:
1. Return the COMPLETE contract as a ```yaml code block
2. Only change what's needed to fix the error
3. Field properties allowed: type, required, description, enum, default, immutable, computed, constraints, references, format, items_type
4. Constraint sub-keys allowed: min, max, maxLength, minLength, pattern — NOTHING ELSE
5. Do NOT add "required_when", "conditional_required", or any property not listed above
6. If a field should be conditionally required, just set required: true — workflow guards handle conditions

Brief explanation first, then the complete YAML.
"""

# Properties that are valid on a field definition
_VALID_FIELD_PROPS = {
    "type", "required", "description", "enum", "default", "immutable",
    "computed", "constraints", "references", "format", "items_type",
}

# Properties that are valid inside constraints
_VALID_CONSTRAINT_PROPS = {"min", "max", "maxLength", "minLength", "pattern"}


def propose_llm_fix(
    ticket: HealerTicket,
    contract: dict,
    diff_root: Path = Path(".forge/diffs"),
) -> Optional[HealerProposal]:
    """Propose a fix using the LLM. Includes sanitization and retry."""
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception as e:
        logger.warning("LLM engine not available: %s", e)
        return None

    contract_yaml = yaml.dump(contract, default_flow_style=False, sort_keys=False)
    store = DiffStore(root=diff_root)
    diff_history = store.format_for_llm(ticket.contract_fqn or "", n=5)

    prompt = _build_prompt(ticket, contract_yaml, diff_history)

    # Attempt 1
    proposal = _attempt_fix(engine, prompt, contract, ticket)
    if proposal:
        return proposal

    # Attempt 2 — retry with simpler prompt
    logger.info("First attempt failed, retrying with simpler prompt")
    simple_prompt = (
        f"Fix this contract. Error: {ticket.raw_error}\n\n"
        f"Just change the minimum fields needed. Do NOT add new properties.\n\n"
        f"```yaml\n{contract_yaml}```"
    )
    return _attempt_fix(engine, simple_prompt, contract, ticket)


def _attempt_fix(engine, prompt: str, contract: dict, ticket: HealerTicket) -> Optional[HealerProposal]:
    """Single attempt: call LLM, sanitize, validate, return proposal or None."""
    try:
        response = engine.ask(question=prompt, system=_SYSTEM_PROMPT)
    except Exception as e:
        logger.error("LLM request failed: %s", e)
        return None

    proposed = _extract_yaml(response)
    if proposed is None:
        logger.warning("Could not parse YAML from LLM response")
        return None

    # Sanitize — strip invalid properties the LLM may have invented
    _sanitize_contract(proposed)

    # Validate
    errors = validate_contract(proposed)
    real_errors = [e for e in errors if e.severity == "error"]
    if real_errors:
        logger.warning("LLM proposal has %d validation errors after sanitization", len(real_errors))
        for e in real_errors:
            logger.warning("  %s: %s", e.path, e.message)
        return None

    changes = compute_diff(contract, proposed)
    if not changes:
        return None

    method = "llm_runtime" if ticket.tier == 3 else "llm_structural"
    return HealerProposal(
        contract_fqn=ticket.contract_fqn or "",
        before=contract,
        after=proposed,
        changes=changes,
        explanation=_extract_explanation(response),
        confidence=0.7 if ticket.tier == 2 else 0.5,
        method=method,
    )


def _sanitize_contract(contract: dict) -> None:
    """Strip invalid properties the LLM may have invented.

    Modifies the contract in-place. Removes any field properties
    not in the valid set, and any constraint sub-keys not in the
    valid constraint set.
    """
    spec = contract.get("spec", {})
    fields = spec.get("fields", {})

    for field_name, field_def in fields.items():
        if not isinstance(field_def, dict):
            continue

        # Strip invalid field properties
        invalid_keys = [k for k in field_def if k not in _VALID_FIELD_PROPS]
        for k in invalid_keys:
            logger.info("Sanitized: removed invalid field property '%s' from %s", k, field_name)
            del field_def[k]

        # Strip invalid constraint properties
        constraints = field_def.get("constraints")
        if isinstance(constraints, dict):
            invalid_constraints = [k for k in constraints if k not in _VALID_CONSTRAINT_PROPS]
            for k in invalid_constraints:
                logger.info("Sanitized: removed invalid constraint '%s' from %s", k, field_name)
                del constraints[k]
            # Remove empty constraints dict
            if not constraints:
                del field_def["constraints"]


def _build_prompt(ticket: HealerTicket, contract_yaml: str, diff_history: str) -> str:
    parts = [
        f"Contract FQN: {ticket.contract_fqn}",
        f"\nError:\n{ticket.raw_error}",
        f"\nCurrent contract:\n```yaml\n{contract_yaml}```",
    ]
    if diff_history and "No change history" not in diff_history:
        parts.append(f"\nRecent change history:\n{diff_history}")
    if ticket.tier == 3 and ticket.context.get("stacktrace"):
        parts.append(f"\nRuntime stacktrace:\n{ticket.context['stacktrace']}")
    return "\n".join(parts)


def _extract_yaml(response: str) -> Optional[dict]:
    match = re.search(r"```ya?ml\s*\n(.*?)```", response, re.DOTALL)
    if match:
        try:
            return yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None
    try:
        return yaml.safe_load(response)
    except yaml.YAMLError:
        return None


def _extract_explanation(response: str) -> str:
    match = re.search(r"```", response)
    if match:
        return response[:match.start()].strip()[:200]
    return response[:200].strip()

"""Shared emitter machinery.

Every emitter ends by handing its assembled dict to :func:`render`, which
normalizes it, validates it against its meta-schema, and only then serializes.
The gate lives here rather than in each caller because the callers are the
problem: `factory chat` used to print schema errors as "warnings" and write the
contract anyway, so an LLM tool call could put a contract that never compiles
into `domains/`. An emitter that cannot produce a valid contract must raise,
the same way a generator that cannot produce valid output raises
`GenerationError` instead of emitting a stub.
"""

from __future__ import annotations

import yaml

from forge.normalize import normalize_contract
from forge.parser.validator import validate_contract


class EmitterError(Exception):
    """Raised when interview data cannot produce a valid contract."""


def render(contract: dict, *, what: str) -> str:
    """Normalize, validate, and serialize *contract*.

    Args:
        contract: The assembled contract dict. Mutated in place by normalization.
        what: Identifier used in the error message (e.g. ``"entity/shop/product"``).

    Returns:
        The contract as a YAML string.

    Raises:
        EmitterError: If the contract fails its meta-schema.
    """
    normalize_contract(contract)

    errors = [e for e in validate_contract(contract) if e.severity == "error"]
    if errors:
        detail = "\n".join(f"  {e.path}: {e.message}" for e in errors)
        raise EmitterError(f"{what} is not a valid contract:\n{detail}")

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)

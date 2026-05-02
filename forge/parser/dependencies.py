"""Extract compiler dependency edges from contract semantics."""

from __future__ import annotations

import re
from typing import Any

_FQN = re.compile(r"^(entity|workflow|page|route|agent|mixin|infra)/[a-zA-Z0-9_./-]+$")


def extract_semantic_dependencies(contract: dict) -> list[str]:
    """Return FQNs referenced by a contract's semantic fields.

    The explicit `requires` array is still supported, but this module is the
    compiler-owned place for deriving graph edges from contract meaning.
    """
    kind = contract.get("kind")
    spec = contract.get("spec", {})
    deps: list[str] = []

    if kind == "Entity":
        _add_many(deps, spec.get("mixins", []))
        _add_one(deps, spec.get("state_machine"))
        for field_def in spec.get("fields", {}).values():
            if not isinstance(field_def, dict):
                continue
            refs = field_def.get("references")
            if isinstance(refs, dict):
                _add_one(deps, refs.get("entity"))
        for agents in spec.get("ai_integration", {}).values():
            _add_many(deps, agents)

    elif kind in {"Route", "Page"}:
        _add_one(deps, spec.get("entity"))
        if kind == "Route":
            for endpoint in spec.get("endpoints", []):
                if isinstance(endpoint, dict):
                    _extract_nested_fqns(endpoint.get("side_effects", []), deps)

    elif kind == "Agent":
        input_spec = spec.get("input", {})
        if isinstance(input_spec, dict):
            _add_one(deps, input_spec.get("entity"))

    elif kind == "Mixin":
        for field_def in spec.get("fields", {}).values():
            if not isinstance(field_def, dict):
                continue
            refs = field_def.get("references")
            if isinstance(refs, dict):
                _add_one(deps, refs.get("entity"))

    # Infra currently has no standardized FQN-bearing fields.
    return deps


def merge_dependencies(contract: dict) -> list[str]:
    """Return explicit and semantic dependencies in deterministic order."""
    deps: list[str] = []
    requires = contract.get("requires", [])
    if isinstance(requires, list):
        _add_many(deps, requires)
    _add_many(deps, extract_semantic_dependencies(contract))
    return deps


def _add_one(deps: list[str], value: Any) -> None:
    if isinstance(value, str) and _FQN.match(value) and value not in deps:
        deps.append(value)


def _add_many(deps: list[str], values: Any) -> None:
    if isinstance(values, str):
        _add_one(deps, values)
        return
    if not isinstance(values, list):
        return
    for value in values:
        _add_one(deps, value)


def _extract_nested_fqns(value: Any, deps: list[str]) -> None:
    if isinstance(value, str):
        _add_one(deps, value)
    elif isinstance(value, list):
        for item in value:
            _extract_nested_fqns(item, deps)
    elif isinstance(value, dict):
        for item in value.values():
            _extract_nested_fqns(item, deps)

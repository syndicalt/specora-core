"""Emit Entity contract YAML from interview data."""
from __future__ import annotations

import yaml

from forge.normalize import normalize_contract


def emit_entity(name: str, domain: str, data: dict) -> str:
    """Convert interview data into a valid Entity contract YAML string.

    Args:
        name: Entity name (snake_case).
        domain: Domain namespace.
        data: Interview data with keys: description, fields, mixins,
              state_machine, number_prefix, icon.

    Returns:
        Valid YAML string matching the Entity meta-schema envelope.
    """
    requires: list[str] = []

    # Add mixin FQNs to requires
    mixins = data.get("mixins", [])
    for m in mixins:
        if m not in requires:
            requires.append(m)

    # Auto-detect references in fields and add entity FQNs to requires
    fields = data.get("fields", {})
    for _field_name, field_def in fields.items():
        ref = field_def.get("references")
        if ref and "entity" in ref:
            entity_fqn = ref["entity"]
            if entity_fqn not in requires:
                requires.append(entity_fqn)

    # Add workflow FQN to requires if present
    state_machine = data.get("state_machine")
    if state_machine and state_machine not in requires:
        requires.append(state_machine)

    # Build spec
    spec: dict = {}

    if data.get("icon"):
        spec["icon"] = data["icon"]

    if data.get("number_prefix"):
        spec["number_prefix"] = data["number_prefix"]

    spec["fields"] = fields

    if mixins:
        spec["mixins"] = list(mixins)

    if state_machine:
        spec["state_machine"] = state_machine

    contract = {
        "apiVersion": "specora.dev/v1",
        "kind": "Entity",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": data.get("description", f"A {name} entity"),
        },
        "requires": requires,
        "spec": spec,
    }

    normalize_contract(contract)

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)

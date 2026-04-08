"""Emit Workflow contract YAML from interview data."""
from __future__ import annotations

import yaml

from forge.normalize import normalize_contract


def emit_workflow(name: str, domain: str, data: dict) -> str:
    """Convert interview data into a valid Workflow contract YAML string.

    Args:
        name: Workflow name (snake_case).
        domain: Domain namespace.
        data: Interview data with keys: initial, states, transitions,
              guards, description.

    Returns:
        Valid YAML string matching the Workflow meta-schema envelope.
    """
    spec: dict = {}

    spec["initial"] = data["initial"]
    spec["states"] = data["states"]
    spec["transitions"] = data["transitions"]

    if data.get("guards"):
        spec["guards"] = data["guards"]

    if data.get("side_effects"):
        spec["side_effects"] = data["side_effects"]

    contract = {
        "apiVersion": "specora.dev/v1",
        "kind": "Workflow",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": data.get("description", f"{name} workflow"),
        },
        "requires": [],
        "spec": spec,
    }

    normalize_contract(contract)

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)

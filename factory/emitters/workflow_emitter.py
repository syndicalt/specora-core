"""Emit Workflow contract YAML from interview data."""

from __future__ import annotations

from factory.emitters.base import EmitterError, render


def emit_workflow(name: str, domain: str, data: dict) -> str:
    """Convert interview data into a valid Workflow contract YAML string.

    Args:
        name: Workflow name (snake_case).
        domain: Domain namespace.
        data: Interview data with keys: initial, states, transitions,
              guards, side_effects, description.

    Returns:
        Valid YAML string matching the Workflow meta-schema envelope.

    Raises:
        EmitterError: If the state machine is incoherent — a missing key, or
            an initial state or transition target that is not declared. The
            meta-schema cannot see those, so an unchecked contract would pass
            validation and then fail the compiler's semantic pass instead.
    """
    for key in ("initial", "states", "transitions"):
        if key not in data:
            raise EmitterError(f"workflow '{name}': missing required key '{key}'")

    states = data["states"]
    transitions = data["transitions"]
    if not isinstance(states, dict) or not states:
        raise EmitterError(f"workflow '{name}': 'states' must be a non-empty mapping")
    if not isinstance(transitions, dict):
        raise EmitterError(f"workflow '{name}': 'transitions' must be a mapping")

    declared = set(states)
    initial = data["initial"]
    if initial not in declared:
        raise EmitterError(
            f"workflow '{name}': initial state {initial!r} is not declared. "
            f"Declared states: {sorted(declared)}"
        )

    undeclared = set()
    for source, targets in transitions.items():
        if source not in declared:
            undeclared.add(source)
        for target in targets if isinstance(targets, list) else [targets]:
            if target not in declared:
                undeclared.add(target)
    if undeclared:
        raise EmitterError(
            f"workflow '{name}': transitions reference undeclared state(s) "
            f"{sorted(undeclared)}. Declared states: {sorted(declared)}"
        )

    spec: dict = {
        "initial": initial,
        "states": states,
        "transitions": transitions,
    }

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

    return render(contract, what=f"workflow/{domain}/{name}")

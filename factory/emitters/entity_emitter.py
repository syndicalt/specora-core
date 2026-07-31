"""Emit Entity contract YAML from interview data."""

from __future__ import annotations

from factory.emitters.base import EmitterError, render


def emit_entity(name: str, domain: str, data: dict) -> str:
    """Convert interview data into a valid Entity contract YAML string.

    Args:
        name: Entity name (snake_case).
        domain: Domain namespace.
        data: Interview data with keys: description, fields, mixins,
              state_machine, number_prefix, icon.

    Returns:
        Valid YAML string matching the Entity meta-schema envelope.

    Raises:
        EmitterError: If the interview data cannot produce a contract that
            passes the Entity meta-schema, or if the entity is provably empty
            (no fields, no mixins, no state machine) and so could never
            generate a repository.
    """
    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        raise EmitterError(
            f"entity '{name}': spec.fields must be a mapping of field name to "
            f"field definition, got {type(fields).__name__}"
        )
    # An LLM asked for `fields` routinely answers `name: string` instead of
    # `name: {type: string}`. Caught here so the operator sees which field is
    # malformed, rather than an AttributeError from the reference scan below.
    malformed = sorted(k for k, v in fields.items() if not isinstance(v, dict))
    if malformed:
        raise EmitterError(
            f"entity '{name}': field(s) {malformed} are not mappings. "
            "Each field must be a mapping with at least a 'type' key."
        )
    mixins = data.get("mixins") or []
    state_machine = data.get("state_machine")

    # Mixins and a bound workflow contribute fields the compiler resolves
    # later, so only the case with none of the three is provably empty. Such
    # an entity passes the meta-schema and then fails generation with
    # "declares no fields" — the interview's LLM-failure fallback used to
    # produce exactly this and write it to disk without a word.
    if not fields and not mixins and not state_machine:
        raise EmitterError(
            f"entity '{name}' has no fields, no mixins, and no state machine. "
            "It would compile to a table with no columns and fail at generation "
            "time. Capture its fields first."
        )

    requires: list[str] = []
    for m in mixins:
        if m not in requires:
            requires.append(m)

    # A referenced entity is a dependency whether or not the interview said so.
    for field_def in fields.values():
        ref = field_def.get("references")
        if isinstance(ref, dict) and "entity" in ref:
            entity_fqn = ref["entity"]
            if entity_fqn not in requires:
                requires.append(entity_fqn)

    if state_machine and state_machine not in requires:
        requires.append(state_machine)

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

    return render(contract, what=f"entity/{domain}/{name}")

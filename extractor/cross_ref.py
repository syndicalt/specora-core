"""Pass 3: Resolve relationships and detect workflows."""

from __future__ import annotations

from extractor.models import (
    Confidence,
    ExtractedEntity,
    ExtractedRoute,
    ExtractedWorkflow,
    safe_contract_name,
)


def cross_reference(
    entities: list[ExtractedEntity],
    routes: list[ExtractedRoute],
    domain: str,
    *,
    warnings: list[str] | None = None,
) -> tuple[list[ExtractedEntity], list[ExtractedRoute], list[ExtractedWorkflow]]:
    """Resolve relationships between extracted entities, routes, and workflows.

    1. Normalize entity and field names to snake_case
    2. Resolve reference fields to FQNs, dropping references with no target
    3. Detect workflows from state fields
    4. Match routes to entities
    """
    notes = warnings if warnings is not None else []
    workflows: list[ExtractedWorkflow] = []

    for entity in entities:
        entity.name = safe_contract_name(entity.name)
    known = {e.name: e for e in entities}

    for entity in entities:
        for field in entity.fields:
            field.name = safe_contract_name(field.name, fallback="")
            if not field.reference_entity:
                continue

            target = _resolve_target(safe_contract_name(field.reference_entity, fallback=""), known)
            if target == entity.name:
                # A self-reference (`parent_account_id`) is a real relationship,
                # but `emit_entity` copies every reference into `requires` and
                # the compiler rejects a contract that requires itself. Keeping
                # it would produce a domain that cannot be built.
                notes.append(
                    f"{entity.name}.{field.name}: self-reference dropped; a contract "
                    f"cannot list itself in `requires`"
                )
                field.reference_entity = ""
                field.reference_edge = ""
            elif target:
                field.reference_entity = f"entity/{domain}/{target}"
                if not field.reference_edge:
                    field.reference_edge = target.upper()
                field.reference_display = _display_field(known[target])
                # Every emitted entity keys on `mixin/stdlib/identifiable`, whose
                # `id` is a uuid. A legacy `Column(Integer, ForeignKey(...))`
                # would otherwise emit an INTEGER column pointing at a UUID
                # primary key, which PostgreSQL cannot build a foreign key on.
                field.type = "uuid"
                field.constraints.pop("maxLength", None)
            else:
                # `requires` naming a contract that was never extracted fails
                # compilation. The field is kept — it is real — but as a plain
                # column rather than a foreign key into nothing.
                notes.append(
                    f"{entity.name}.{field.name}: reference target "
                    f"{field.reference_entity!r} was not extracted; emitted as a plain field"
                )
                field.reference_entity = ""
                field.reference_edge = ""

        if entity.state_field and len(entity.state_values) >= 2:
            named = (safe_contract_name(v, fallback="") for v in entity.state_values)
            states = [s for s in dict.fromkeys(named) if s]
            if len(states) >= 2:
                workflows.append(
                    ExtractedWorkflow(
                        name=f"{entity.name}_lifecycle",
                        entity_name=entity.name,
                        states=states,
                        initial=states[0],
                        source_file=entity.source_file,
                        confidence=Confidence.MEDIUM,
                    )
                )

    for route in routes:
        if route.entity_name:
            route.entity_name = safe_contract_name(route.entity_name, fallback="")

    return entities, routes, workflows


def _resolve_target(target: str, known: dict[str, ExtractedEntity]) -> str:
    """Find the entity a reference names, allowing a qualifying prefix.

    `assigned_agent_id` and `parent_account_id` name the role a row plays, not
    a separate table. Dropping the leading qualifiers recovers `agent` and
    `account`; without this the reference is discarded and the emitted contract
    loses a relationship the source really has.
    """
    if not target:
        return ""
    parts = target.split("_")
    for start in range(len(parts)):
        candidate = "_".join(parts[start:])
        if candidate in known:
            return candidate
    return ""


# Fields worth showing a human in place of a UUID, most specific first.
_DISPLAY_CANDIDATES = (
    "name",
    "title",
    "subject",
    "label",
    "code",
    "reference",
    "number",
    "email",
)


def _display_field(target: ExtractedEntity) -> str:
    """Pick a display field that the target entity actually has.

    `forge.ir.compiler` defaults an omitted `display` to `"name"` and then
    `forge.ir.semantic` rejects the contract if the target has no such field,
    so leaving this blank is not an option — something real has to be named.
    """
    by_name = {f.name: f for f in target.fields}
    for candidate in _DISPLAY_CANDIDATES:
        if candidate in by_name:
            return candidate
    for field in target.fields:
        if field.type in ("string", "text"):
            return field.name
    return target.fields[0].name if target.fields else ""

"""Pass 3: Resolve relationships and detect workflows."""
from __future__ import annotations

from extractor.models import (
    Confidence,
    ExtractedEntity,
    ExtractedRoute,
    ExtractedWorkflow,
)
from forge.normalize import normalize_name


def cross_reference(
    entities: list[ExtractedEntity],
    routes: list[ExtractedRoute],
    domain: str,
) -> tuple[list[ExtractedEntity], list[ExtractedRoute], list[ExtractedWorkflow]]:
    """Resolve relationships between extracted entities, routes, and workflows.

    1. Normalize entity names to snake_case
    2. Resolve reference_entity fields to FQNs
    3. Detect workflows from state fields
    4. Match routes to entities
    """
    entity_names = {normalize_name(e.name): e for e in entities}
    workflows: list[ExtractedWorkflow] = []

    # Normalize entity names and resolve references
    for entity in entities:
        entity.name = normalize_name(entity.name)

        for field in entity.fields:
            if field.reference_entity:
                ref_name = normalize_name(field.reference_entity)
                if ref_name in entity_names:
                    field.reference_entity = f"entity/{domain}/{ref_name}"
                else:
                    field.reference_entity = f"entity/{domain}/{ref_name}"

                if not field.reference_edge:
                    field.reference_edge = field.name.upper().replace("_ID", "")

        # Detect workflows from state fields
        if entity.state_field and entity.state_values and len(entity.state_values) >= 2:
            wf_name = f"{entity.name}_lifecycle"
            workflows.append(ExtractedWorkflow(
                name=wf_name,
                entity_name=entity.name,
                states=entity.state_values,
                initial=entity.state_values[0],
                source_file=entity.source_file,
                confidence=Confidence.MEDIUM,
            ))

    # Match routes to entities
    for route in routes:
        if route.entity_name:
            route.entity_name = normalize_name(route.entity_name)

    return entities, routes, workflows

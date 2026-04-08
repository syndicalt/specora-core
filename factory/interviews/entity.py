"""Entity interview — conversational entity field discovery.

Interviews the user about a single entity: its purpose, fields,
references, enums, and lifecycle. Uses the LLM to infer field types
and detect references to other entities.

Usage:
    from factory.interviews.entity import run_entity_interview

    data = run_entity_interview(engine, "patient", "veterinary",
                                 existing_entities=["owner"])
"""

from __future__ import annotations

import logging

from engine.context import build_system_prompt
from engine.engine import LLMEngine
from factory.interviews.base import Interview, InterviewLLMError, InterviewParseError

logger = logging.getLogger(__name__)


def run_entity_interview(
    engine: LLMEngine,
    entity_name: str,
    domain: str,
    description: str = "",
    existing_entities: list[str] | None = None,
) -> dict:
    """Run an interactive interview to define an entity's fields.

    Args:
        engine: The LLM engine.
        entity_name: Name of the entity being defined.
        domain: Domain name.
        description: Brief description (from domain discovery).
        existing_entities: Other entities in this domain (for reference detection).

    Returns:
        Dict with keys: description, fields, mixins, state_machine (optional)
    """
    system = build_system_prompt(
        "entity_interview",
        domain=domain,
        existing_entities=existing_entities or [],
    )
    interview = Interview(engine, system_prompt=system, mode="entity interview", domain=domain)

    interview.show(f"[bold cyan]── Entity: {entity_name.replace('_', ' ').title()} ──[/bold cyan]")

    if not description:
        description = interview.ask_user(
            f"Describe what a '{entity_name}' is in one sentence\n"
            f"  [dim]Example: \"A task that can be assigned to a user with a due date and priority\"[/dim]"
        )

    fields_input = interview.ask_user(
        f"What fields does a {entity_name} have?\n"
        f"  [dim]List the data you want to store, comma-separated.[/dim]\n"
        f"  [dim]Example: name, email, status, priority, due date, assigned to[/dim]"
    )

    instruction = f"""
The user described a '{entity_name}' entity: {description}
They said it has these fields: {fields_input}

Generate a YAML mapping of field definitions. For each field, include:
- type (from the valid types list)
- description (brief)
- required: true if it seems essential
- enum: [...] if the field has a fixed set of values
- references: if the field points to another entity (check existing entities: {existing_entities or []})

Also include:
- mixins: list of mixin FQNs to include
- state_machine_needed: true/false
- description: one-sentence entity description

Format:
fields:
  field_name:
    type: string
    required: true
    description: "..."
mixins:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable
state_machine_needed: false
description: "..."
"""
    try:
        structured = interview.ask_llm_structured(fields_input, instruction)
    except InterviewLLMError as e:
        interview.show(f"[red]AI provider error:[/red] {e}")
        interview.show("[yellow]Using basic field defaults. You can edit the contracts later.[/yellow]")
        structured = {
            "fields": {},
            "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
        }
    except InterviewParseError:
        interview.show("[yellow]Couldn't parse the field structure. Let me try again...[/yellow]")
        structured = {
            "fields": {},
            "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
        }

    fields = structured.get("fields", {})
    mixins = structured.get("mixins", ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"])
    needs_workflow = structured.get("state_machine_needed", False)
    entity_desc = structured.get("description", description)

    # Ask about lifecycle
    workflow_fqn = None
    if needs_workflow:
        interview.show(f"[dim]It looks like {entity_name} has a lifecycle.[/dim]")
        if interview.confirm(f"Does {entity_name} have a state machine?"):
            workflow_fqn = f"workflow/{domain}/{entity_name}_lifecycle"
    else:
        if interview.confirm(f"Does {entity_name} have a lifecycle (state machine)?"):
            workflow_fqn = f"workflow/{domain}/{entity_name}_lifecycle"

    result = {
        "description": entity_desc,
        "fields": fields,
        "mixins": mixins,
    }
    if workflow_fqn:
        result["state_machine"] = workflow_fqn

    return result

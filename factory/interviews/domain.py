"""Domain discovery interview — the opening conversation.

Discovers what the user is building: the domain name, description,
and initial set of entities. This is the first phase of `specora factory new`.

Usage:
    from factory.interviews.domain import run_domain_interview

    domain, description, entities = run_domain_interview(engine)
"""

from __future__ import annotations

import logging

from engine.context import build_system_prompt
from engine.engine import LLMEngine
from factory.interviews.base import Interview, InterviewLLMError, InterviewParseError

logger = logging.getLogger(__name__)


def run_domain_interview(engine: LLMEngine) -> tuple[str, str, list[dict]]:
    """Run the domain discovery interview.

    Asks the user what they're building, then uses the LLM to infer
    a domain name, description, and initial entity list.

    Args:
        engine: The LLM engine.

    Returns:
        Tuple of (domain_name, description, entities) where entities
        is a list of dicts with 'name' and 'description' keys.
    """
    system = build_system_prompt("domain_discovery")
    interview = Interview(engine, system_prompt=system, mode="domain discovery")

    interview.show("[bold]Welcome to the Specora Factory.[/bold]")
    interview.show("[dim]I'll help you define your domain through conversation.[/dim]")

    purpose = interview.ask_user("What are you building?")

    instruction = """
Based on the user's description, suggest:
1. A short snake_case domain name (e.g., "veterinary", "logistics", "healthcare")
2. A one-sentence description
3. The core entities (3-8) with brief descriptions

Format as YAML:
domain: name
description: "one sentence"
entities:
  - name: entity_name
    description: "brief description"
  - name: entity_name
    description: "brief description"
"""

    try:
        structured = interview.ask_llm_structured(purpose, instruction)
    except (InterviewLLMError, InterviewParseError) as e:
        if isinstance(e, InterviewLLMError):
            interview.show(f"[red]AI provider error:[/red] {e}")
            interview.show("[yellow]Falling back to manual input.[/yellow]")
        else:
            interview.show("[yellow]Let me ask more specifically...[/yellow]")
        interview.show("")
        return _manual_domain_input(interview)

    domain_name = structured.get("domain", "my_domain")
    description = structured.get("description", purpose)
    entities = structured.get("entities", [])

    # Show what we inferred and confirm
    interview.show(f"\n  [bold]Domain:[/bold] {domain_name}")
    interview.show(f"  [bold]Description:[/bold] {description}")
    interview.show(f"  [bold]Entities:[/bold]")
    for e in entities:
        interview.show(f"    - {e['name']}: {e.get('description', '')}")

    if not interview.confirm("\n  Does this look right?"):
        domain_name = interview.ask_user("Domain name (snake_case):")
        description = interview.ask_user("Description:")
        entities_raw = interview.ask_user("Entities (comma-separated):")
        entities = [
            {"name": e.strip().lower().replace(" ", "_"), "description": ""}
            for e in entities_raw.split(",")
        ]

    return domain_name, description, entities


def _manual_domain_input(interview: Interview) -> tuple[str, str, list[dict]]:
    """Collect domain info manually with helpful examples."""
    domain_name = interview.ask_user(
        'Domain name (one word, snake_case)\n'
        '  [dim]Examples: "todolist", "veterinary", "ecommerce", "helpdesk"[/dim]'
    )
    desc = interview.ask_user(
        'Describe what this system does in one sentence\n'
        '  [dim]Example: "Manage tasks, projects, and team assignments for a productivity app"[/dim]'
    )
    entities_raw = interview.ask_user(
        'What are the core things (entities) this system manages?\n'
        '  [dim]These become your data models — the nouns of your system.[/dim]\n'
        '  [dim]Enter them comma-separated. Examples:[/dim]\n'
        '  [dim]  To-do app:   task, project, label, user[/dim]\n'
        '  [dim]  Vet clinic:  patient, owner, appointment, veterinarian, medical_record[/dim]\n'
        '  [dim]  E-commerce:  product, order, customer, review, category[/dim]\n'
        '  [dim]  Help desk:   ticket, agent, customer, knowledge_article[/dim]'
    )
    entities = [
        {"name": e.strip().lower().replace(" ", "_"), "description": ""}
        for e in entities_raw.split(",")
        if e.strip()
    ]
    return domain_name, desc, entities

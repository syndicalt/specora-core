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
from factory.paths import UnsafeNameError, safe_name

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
    entities = _clean_entities(structured.get("entities"), interview)

    try:
        domain_name = safe_name(domain_name, what="domain name")
    except UnsafeNameError as exc:
        # The domain name becomes a directory under domains/, so an
        # unconstrained one from the model is a path, not just a label.
        interview.show(f"[yellow]The model suggested an unusable domain name:[/yellow] {exc}")
        return _manual_domain_input(interview)

    if not entities:
        interview.show("[yellow]The model named no usable entities.[/yellow]")
        return _manual_domain_input(interview)

    # Show what we inferred and confirm
    interview.show(f"\n  [bold]Domain:[/bold] {domain_name}")
    interview.show(f"  [bold]Description:[/bold] {description}")
    interview.show("  [bold]Entities:[/bold]")
    for e in entities:
        interview.show(f"    - {e['name']}: {e.get('description', '')}")

    if not interview.confirm("\n  Does this look right?"):
        return _manual_domain_input(interview)

    return domain_name, description, entities


def _clean_entities(raw: object, interview: Interview) -> list[dict]:
    """Keep only the entities the model named with a usable snake_case name."""
    if not isinstance(raw, list):
        return []

    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            interview.show(f"[yellow]Ignoring malformed entity from the model:[/yellow] {item!r}")
            continue
        try:
            name = safe_name(item["name"], what="entity name")
        except UnsafeNameError as exc:
            interview.show(f"[yellow]Ignoring entity with an unusable name:[/yellow] {exc}")
            continue
        cleaned.append({"name": name, "description": str(item.get("description", ""))})
    return cleaned


def _manual_domain_input(interview: Interview) -> tuple[str, str, list[dict]]:
    """Collect domain info manually with helpful examples.

    Raises:
        InterviewInputError: If the user cannot supply a usable domain name or
            any usable entity name. Proceeding with a placeholder would put
            contracts in a directory the user did not ask for.
    """
    domain_name = _ask_until_valid(
        interview,
        "Domain name (one word, snake_case)\n"
        '  [dim]Examples: "todolist", "veterinary", "ecommerce", "helpdesk"[/dim]',
        what="domain name",
    )
    desc = interview.ask_user(
        "Describe what this system does in one sentence\n"
        '  [dim]Example: "Manage tasks, projects, and team assignments '
        'for a productivity app"[/dim]'
    )
    entities_raw = interview.ask_user(
        "What are the core things (entities) this system manages?\n"
        "  [dim]These become your data models — the nouns of your system.[/dim]\n"
        "  [dim]Enter them comma-separated. Examples:[/dim]\n"
        "  [dim]  To-do app:   task, project, label, user[/dim]\n"
        "  [dim]  Vet clinic:  patient, owner, appointment, veterinarian, medical_record[/dim]\n"
        "  [dim]  E-commerce:  product, order, customer, review, category[/dim]\n"
        "  [dim]  Help desk:   ticket, agent, customer, knowledge_article[/dim]"
    )
    entities: list[dict] = []
    for chunk in entities_raw.split(","):
        label = chunk.strip()
        if not label:
            continue
        try:
            name = safe_name(label.replace(" ", "_"), what="entity name")
        except UnsafeNameError as exc:
            interview.show(f"[yellow]Skipping '{label}':[/yellow] {exc}")
            continue
        if not any(e["name"] == name for e in entities):
            entities.append({"name": name, "description": ""})

    if not entities:
        raise InterviewInputError("No usable entity names were given; nothing to build.")

    return domain_name, desc, entities


def _ask_until_valid(interview: Interview, prompt: str, *, what: str, attempts: int = 3) -> str:
    """Ask for a snake_case identifier, re-prompting on a bad answer.

    Bounded because the caller may be running against a closed stdin, where an
    unbounded loop would spin instead of reporting the problem.
    """
    for _ in range(attempts):
        try:
            return safe_name(interview.ask_user(prompt), what=what)
        except UnsafeNameError as exc:
            interview.show(f"[red]{exc}[/red]")
    raise InterviewInputError(f"No usable {what} after {attempts} attempts.")


class InterviewInputError(Exception):
    """Raised when the user cannot supply an input the Factory requires."""

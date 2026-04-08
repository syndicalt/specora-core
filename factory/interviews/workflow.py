"""Workflow interview — conversational state machine discovery.

Interviews the user about an entity's lifecycle: states, transitions,
guards, and terminal states. Uses the LLM to structure the responses.

Usage:
    from factory.interviews.workflow import run_workflow_interview

    data = run_workflow_interview(engine, "patient_lifecycle", "veterinary",
                                   entity_name="patient")
"""

from __future__ import annotations

import logging

from engine.context import build_system_prompt
from engine.engine import LLMEngine
from factory.interviews.base import Interview, InterviewLLMError, InterviewParseError

logger = logging.getLogger(__name__)


def run_workflow_interview(
    engine: LLMEngine,
    workflow_name: str,
    domain: str,
    entity_name: str = "",
) -> dict:
    """Run an interactive interview to define a state machine.

    Args:
        engine: The LLM engine.
        workflow_name: Name of the workflow.
        domain: Domain name.
        entity_name: The entity this workflow is for.

    Returns:
        Dict with keys: initial, states, transitions, guards, description
    """
    system = build_system_prompt("workflow_interview", domain=domain)
    interview = Interview(engine, system_prompt=system, mode="workflow interview", domain=domain)

    interview.show(f"[bold cyan]── Workflow: {entity_name} lifecycle ──[/bold cyan]")

    states_input = interview.ask_user(
        f"What states can a {entity_name} be in?\n"
        f"  [dim]List the lifecycle stages, comma-separated. Examples:[/dim]\n"
        f"  [dim]  Task:    todo, in_progress, done, cancelled[/dim]\n"
        f"  [dim]  Order:   pending, confirmed, shipped, delivered, returned[/dim]\n"
        f"  [dim]  Ticket:  new, assigned, in_progress, resolved, closed[/dim]"
    )

    instruction = f"""
The user is defining a lifecycle for '{entity_name}'.
They said the states are: {states_input}

Generate a YAML workflow with:
- initial: the starting state
- states: each state with label, category (open/hold/closed), and terminal flag
- transitions: valid state transitions
- guards: required fields for transitions (if any)
- description: one-sentence workflow description

Format:
initial: state_name
states:
  state_name:
    label: "Human Label"
    category: open
    terminal: false
transitions:
  state_name:
    - other_state
guards:
  "from_state -> to_state":
    require_fields: [field_name]
description: "..."
"""

    try:
        structured = interview.ask_llm_structured(states_input, instruction)
    except InterviewLLMError as e:
        interview.show(f"[red]AI provider error:[/red] {e}")
        interview.show("[yellow]Using simple defaults. You can edit the contracts later.[/yellow]")
        structured = {
            "initial": "active",
            "states": {
                "active": {"label": "Active", "category": "open"},
                "inactive": {"label": "Inactive", "category": "closed"},
            },
            "transitions": {"active": ["inactive"], "inactive": ["active"]},
        }
    except InterviewParseError:
        interview.show("[yellow]Couldn't parse workflow. Using simple defaults.[/yellow]")
        structured = {
            "initial": "active",
            "states": {
                "active": {"label": "Active", "category": "open"},
                "inactive": {"label": "Inactive", "category": "closed"},
            },
            "transitions": {"active": ["inactive"], "inactive": ["active"]},
        }

    return {
        "initial": structured.get("initial", "active"),
        "states": structured.get("states", {}),
        "transitions": structured.get("transitions", {}),
        "guards": structured.get("guards", {}),
        "description": structured.get("description", f"Lifecycle for {entity_name}"),
    }

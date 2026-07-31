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
from factory.paths import UnsafeNameError, safe_name

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
    except (InterviewLLMError, InterviewParseError) as e:
        if isinstance(e, InterviewLLMError):
            interview.show(f"[red]AI provider error:[/red] {e}")
        else:
            interview.show(f"[yellow]Couldn't parse the workflow:[/yellow] {e}")
        logger.warning(
            "Workflow interview for '%s' fell back to a literal chain: %s", entity_name, e
        )
        # The old fallback replaced the user's states with active/inactive and
        # said nothing about it, so the lifecycle they had just typed was
        # silently discarded.
        structured = _chain_from_raw_input(states_input)
        interview.show(
            f"[yellow]Falling back to a linear chain through the states you typed: "
            f"{', '.join(structured['states'])}.[/yellow]"
        )

    states = structured.get("states")
    transitions = structured.get("transitions")
    if not isinstance(states, dict) or not states or not isinstance(transitions, dict):
        interview.show(
            "[yellow]The model's states/transitions were not usable. "
            "Falling back to a linear chain through the states you typed.[/yellow]"
        )
        structured = _chain_from_raw_input(states_input)
        states = structured["states"]
        transitions = structured["transitions"]

    return {
        "initial": structured.get("initial", next(iter(states))),
        "states": states,
        "transitions": transitions,
        "guards": structured.get("guards", {}),
        "description": structured.get("description", f"Lifecycle for {entity_name}"),
    }


def _chain_from_raw_input(raw: str) -> dict:
    """Build a linear state machine from a comma-separated list of states.

    A chain is the only ordering the raw input actually carries. It is a
    guess, but it is a guess made from what the user said, and every state
    they named survives into the contract where they can rewire it.
    """
    names: list[str] = []
    for chunk in raw.split(","):
        label = chunk.strip()
        if not label:
            continue
        try:
            name = safe_name(label.replace(" ", "_"), what="state name")
        except UnsafeNameError as exc:
            logger.warning("Dropping unusable state label %r: %s", label, exc)
            continue
        if name not in names:
            names.append(name)

    if not names:
        names = ["active", "inactive"]

    states: dict[str, dict] = {}
    transitions: dict[str, list[str]] = {}
    for index, name in enumerate(names):
        is_last = index == len(names) - 1
        states[name] = {
            "label": name.replace("_", " ").title(),
            "category": "closed" if is_last else "open",
        }
        if is_last:
            # The compiler rejects a dead-end state that is not declared
            # terminal, so the chain's end must say so.
            states[name]["terminal"] = True
        else:
            transitions[name] = [names[index + 1]]

    return {"initial": names[0], "states": states, "transitions": transitions}

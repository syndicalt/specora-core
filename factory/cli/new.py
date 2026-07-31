"""specora factory new — full domain bootstrap from conversation.

The showstopper command. Interviews the user about their domain,
generates all contracts (entities, workflows, routes, pages),
opens them in $EDITOR for review, and writes them atomically.

Usage:
    specora factory new
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from factory.emitters.base import EmitterError
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.page_emitter import emit_page, page_columns
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from factory.interviews.domain import InterviewInputError, run_domain_interview
from factory.interviews.entity import run_entity_interview
from factory.interviews.workflow import run_workflow_interview
from factory.paths import UnsafeNameError, safe_name, write_atomic
from factory.preview.editor import preview_contracts
from factory.session import Session
from forge.error_display import format_errors_rich
from forge.parser.validator import ContractValidationError, validate_contract
from forge.targets.naming import pluralize

logger = logging.getLogger(__name__)
console = Console()


SPLASH = """
[bold cyan]
    ____                                ______           __
   / ___/ ____   ___  _________  ____ _/ ____/___ ______/ /_____  _______  __
   \\__ \\ / __ \\ / _ \\/ ___/ __ \\/ ___/ /_  / __ `/ ___/ __/ __ \\/ ___/ / / /
  ___/ // /_/ //  __/ /__/ /_/ / /  / __/ / /_/ / /__/ /_/ /_/ / /  / /_/ /
 /____// .___/ \\___/\\___/\\____/_/  /_/    \\__,_/\\___/\\__/\\____/_/   \\__, /
      /_/                                                           /____/
[/bold cyan]
[dim]  Contract-Driven Development Engine   |   Domain Builder[/dim]
"""


@click.command("new")
@click.option(
    "--input",
    "-i",
    "input_dir",
    default="domains/",
    type=click.Path(),
    help="Base directory for contract output (default: domains/)",
)
def factory_new(input_dir: str) -> None:
    """Bootstrap a new domain from a conversational interview."""
    contracts_base = Path(input_dir)

    console.print(SPLASH)

    # Initialize LLM engine
    try:
        engine = LLMEngine.from_env()
        console.print(f"  [dim]Model: {engine.model_id}  |  Strategy: {engine.strategy}[/dim]")
        console.print()
    except EngineConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Check for resumable session
    session = Session()
    if session.can_resume():
        console.print("[yellow]Found a saved session.[/yellow]")
        if click.confirm("  Resume previous session?", default=True):
            session.resume()
            console.print(
                f"[green]Resumed:[/green] domain '{session.state.domain}' "
                f"(phase: {session.state.phase})"
            )
        else:
            session.cleanup()

    # Phase 1: Domain discovery
    if session.state.phase == "domain_discovery" or not session.state.domain:
        try:
            domain, description, entities = run_domain_interview(engine)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Session saved. Run 'specora factory new' to resume.[/yellow]")
            session.save()
            sys.exit(0)
        except InterviewInputError as e:
            console.print(f"\n[red]{e}[/red]")
            sys.exit(1)

        session.start(domain, description)
        session.state.entities_discovered = [e["name"] for e in entities]
        for e in entities:
            session.state.entity_data[e["name"]] = {"description": e.get("description", "")}
        session.state.phase = "entity_interview"
        session.save()

    # The domain becomes a directory under contracts_base, and a resumed
    # session file can carry any string at all.
    try:
        domain = safe_name(session.state.domain, what="domain name")
    except UnsafeNameError as e:
        console.print(f"[red]Session has an unusable domain name:[/red] {e}")
        sys.exit(1)
    session.state.domain = domain
    console.print(f"\n[bold]Building domain: {domain}[/bold]")

    # Phase 2: Entity interviews
    if session.state.phase == "entity_interview":
        existing = list(session.state.entity_data.keys())
        for entity_name in session.state.entities_discovered:
            if session.state.entity_data.get(entity_name, {}).get("fields"):
                continue

            try:
                desc = session.state.entity_data.get(entity_name, {}).get("description", "")
                data = run_entity_interview(
                    engine,
                    entity_name,
                    domain,
                    description=desc,
                    existing_entities=existing,
                )
                session.add_entity(entity_name, data)
                session.save()
            except (KeyboardInterrupt, EOFError):
                console.print(
                    "\n[yellow]Session saved. Resume with 'specora factory new'.[/yellow]"
                )
                session.save()
                sys.exit(0)

        session.state.phase = "workflow_interview"
        session.save()

    # Phase 3: Workflow interviews
    if session.state.phase == "workflow_interview":
        for entity_name, data in session.state.entity_data.items():
            workflow_fqn = data.get("state_machine")
            if not workflow_fqn:
                continue
            workflow_name = workflow_fqn.split("/")[-1]

            if workflow_name in session.state.workflow_data:
                continue

            try:
                wf_data = run_workflow_interview(
                    engine, workflow_name, domain, entity_name=entity_name
                )
                session.add_workflow(workflow_name, wf_data)
                session.save()
            except (KeyboardInterrupt, EOFError):
                console.print(
                    "\n[yellow]Session saved. Resume with 'specora factory new'.[/yellow]"
                )
                session.save()
                sys.exit(0)

        session.state.phase = "emit"
        session.save()

    # Phase 4: Emit contracts
    if session.state.phase == "emit":
        try:
            contracts = _emit_domain(domain, session)
        except (EmitterError, UnsafeNameError) as exc:
            console.print(f"\n[red]Cannot emit contracts:[/red] {escape(str(exc))}")
            console.print("[yellow]Session saved. Fix the input and re-run to resume.[/yellow]")
            session.save()
            return

        # Belt and braces: the emitters already validate, so a failure here
        # means an emitter and its meta-schema disagree.
        validation_errors = _validate_emitted_contracts(contracts)
        if validation_errors:
            console.print()
            console.print(
                Panel(
                    format_errors_rich(validation_errors),
                    title="[red bold]Validation Errors[/red bold]",
                    border_style="red",
                )
            )
            console.print(
                "[red]Factory produced invalid contracts. This is a bug — please report it.[/red]"
            )
            session.save()
            return

        console.print(f"\n[bold]Generated {len(contracts)} contracts for domain '{domain}'[/bold]")
        accepted, final_contracts = preview_contracts(contracts, domain=domain)

        if not accepted:
            console.print("[yellow]Cancelled. Session saved for later.[/yellow]")
            session.save()
            return

        # The preview opens $EDITOR and reads the files back, so what is about
        # to be written is not what was validated above.
        edit_errors = _validate_emitted_contracts(final_contracts)
        if edit_errors:
            console.print()
            console.print(
                Panel(
                    format_errors_rich(edit_errors),
                    title="[red bold]Edited contracts are invalid[/red bold]",
                    border_style="red",
                )
            )
            console.print("[yellow]Nothing written. Session saved for later.[/yellow]")
            session.save()
            return

        domain_path = contracts_base / domain
        for rel_path, content in final_contracts.items():
            file_path = domain_path / rel_path
            write_atomic(file_path, content)
            console.print(f"  [green]wrote[/green] {file_path}")

        session.cleanup()

        console.print(
            f"\n[bold green]Domain '{domain}' created with "
            f"{len(final_contracts)} contracts.[/bold green]"
        )
        console.print()
        console.print("Next steps:")
        console.print(f"  specora forge validate {domain_path}")
        console.print(f"  specora forge generate {domain_path}")


def _emit_domain(domain: str, session: Session) -> dict[str, str]:
    """Emit every contract for the interviewed domain, keyed by relative path.

    Raises:
        EmitterError: If any contract would be invalid.
        UnsafeNameError: If an interviewed name cannot be used as a filename.
    """
    contracts: dict[str, str] = {}

    for entity_name, data in session.state.entity_data.items():
        stem = safe_name(entity_name, what="entity name")
        contracts[f"entities/{stem}.contract.yaml"] = emit_entity(entity_name, domain, data)

    for wf_name, wf_data in session.state.workflow_data.items():
        stem = safe_name(wf_name, what="workflow name")
        contracts[f"workflows/{stem}.contract.yaml"] = emit_workflow(wf_name, domain, wf_data)

    for entity_name, data in session.state.entity_data.items():
        stem = safe_name(entity_name, what="entity name")
        entity_fqn = f"entity/{domain}/{stem}"
        workflow_fqn = data.get("state_machine", "")
        plural = pluralize(stem)

        contracts[f"routes/{plural}.contract.yaml"] = emit_route(
            plural, domain, entity_fqn, workflow_fqn
        )
        contracts[f"pages/{plural}.contract.yaml"] = emit_page(
            plural, domain, entity_fqn, page_columns(data.get("fields") or {})
        )

    return contracts


def _validate_emitted_contracts(
    contracts: dict[str, str],
) -> list[ContractValidationError]:
    """Parse and validate all emitted YAML contracts.

    Returns list of errors (empty = all valid).
    """
    all_errors: list[ContractValidationError] = []
    for rel_path, yaml_str in contracts.items():
        try:
            contract = yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            all_errors.append(
                ContractValidationError(
                    contract_fqn=rel_path,
                    path="<yaml>",
                    message=f"Invalid YAML: {e}",
                )
            )
            continue

        errors = validate_contract(contract)
        all_errors.extend(errors)
    return all_errors

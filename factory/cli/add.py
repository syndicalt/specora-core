"""specora factory add — add a single contract to an existing domain."""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.syntax import Syntax

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.page_emitter import emit_page
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from factory.interviews.entity import run_entity_interview
from factory.interviews.workflow import run_workflow_interview
from forge.normalize import normalize_name
from forge.parser.validator import validate_contract

console = Console()

VALID_KINDS = ["entity", "workflow", "route", "page"]
KIND_SUBDIRS = {
    "entity": "entities",
    "workflow": "workflows",
    "route": "routes",
    "page": "pages",
}


@click.command("add")
@click.argument("kind", type=click.Choice(VALID_KINDS))
@click.option("--domain", "-d", required=True, help="Target domain name")
@click.option("--name", "-n", required=True, help="Contract name (snake_case)")
@click.option("--entity", "-e", default="", help="Entity FQN (required for route/page)")
@click.option("--input", "-i", "input_dir", default="domains/", type=click.Path(),
              help="Base directory for contracts (default: domains/)")
def factory_add(kind: str, domain: str, name: str, entity: str, input_dir: str) -> None:
    """Add a single contract to an existing domain via LLM interview."""
    domain_path = Path(input_dir) / domain
    if not domain_path.exists():
        console.print(f"[red]Domain not found:[/red] {domain_path}")
        console.print(f"  Run 'specora init {domain}' or 'specora factory new' first.")
        sys.exit(1)

    safe_name = normalize_name(name)

    # Check if contract already exists
    subdir = KIND_SUBDIRS[kind]
    target = domain_path / subdir / f"{safe_name}.contract.yaml"
    if target.exists():
        console.print(f"[red]Contract already exists:[/red] {target}")
        sys.exit(1)

    # Route and page require an entity
    if kind in ("route", "page") and not entity:
        console.print(f"[red]--entity is required for {kind} contracts[/red]")
        console.print(f"  Example: --entity entity/{domain}/{safe_name.rstrip('s')}")
        sys.exit(1)

    # Initialize LLM engine for interview-based kinds
    engine = None
    if kind in ("entity", "workflow"):
        try:
            engine = LLMEngine.from_env()
            console.print(f"  [dim]Model: {engine.model_id}[/dim]")
        except EngineConfigError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    yaml_str = _generate_contract(kind, safe_name, domain, entity, engine)
    if yaml_str is None:
        return

    # Validate
    contract = yaml.safe_load(yaml_str)
    errors = validate_contract(contract)
    real_errors = [e for e in errors if e.severity == "error"]
    if real_errors:
        console.print("[red]Generated contract has validation errors:[/red]")
        for e in real_errors:
            console.print(f"  {e.path}: {e.message}")
        sys.exit(1)

    # Preview
    console.print(f"\n[bold]{kind}/{domain}/{safe_name}[/bold]")
    console.print(Syntax(yaml_str, "yaml", theme="monokai", line_numbers=True))
    response = console.input("\n[bold]Write this contract? [Y/n] [/bold]").strip().lower()
    if response not in ("", "y", "yes"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # Write
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml_str, encoding="utf-8")
    console.print(f"[green]wrote[/green] {target}")


def _generate_contract(kind, name, domain, entity_fqn, engine):
    """Generate a contract via interview or mechanical emission."""
    if kind == "entity":
        try:
            data = run_entity_interview(engine, name, domain)
            return emit_entity(name, domain, data)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Cancelled.[/yellow]")
            return None

    if kind == "workflow":
        try:
            data = run_workflow_interview(engine, name, domain)
            return emit_workflow(name, domain, data)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Cancelled.[/yellow]")
            return None

    if kind == "route":
        return emit_route(name, domain, entity_fqn)

    if kind == "page":
        # Minimal field names — user can refine later
        return emit_page(name, domain, entity_fqn, ["name"])

    return None

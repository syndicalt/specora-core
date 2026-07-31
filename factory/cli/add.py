"""specora factory add — add a single contract to an existing domain."""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from factory.emitters.base import EmitterError
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.page_emitter import emit_page, page_columns
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from factory.interviews.entity import run_entity_interview
from factory.interviews.workflow import run_workflow_interview
from factory.paths import UnsafeNameError, contract_path, write_atomic

console = Console()

VALID_KINDS = ["entity", "workflow", "route", "page"]


@click.command("add")
@click.argument("kind", type=click.Choice(VALID_KINDS))
@click.option("--domain", "-d", required=True, help="Target domain name")
@click.option("--name", "-n", required=True, help="Contract name (snake_case)")
@click.option("--entity", "-e", default="", help="Entity FQN (required for route/page)")
@click.option(
    "--input",
    "-i",
    "input_dir",
    default="domains/",
    type=click.Path(),
    help="Base directory for contracts (default: domains/)",
)
def factory_add(kind: str, domain: str, name: str, entity: str, input_dir: str) -> None:
    """Add a single contract to an existing domain via LLM interview."""
    contracts_base = Path(input_dir)
    try:
        target = contract_path(contracts_base, domain, kind, name)
    except UnsafeNameError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    stem = target.name.removesuffix(".contract.yaml")
    domain_path = contracts_base / domain

    if not domain_path.exists():
        console.print(f"[red]Domain not found:[/red] {domain_path}")
        console.print(f"  Run 'specora init {domain}' or 'specora factory new' first.")
        sys.exit(1)

    if target.exists():
        console.print(f"[red]Contract already exists:[/red] {target}")
        sys.exit(1)

    # Route and page require an entity
    if kind in ("route", "page") and not entity:
        console.print(f"[red]--entity is required for {kind} contracts[/red]")
        console.print(f"  Example: --entity entity/{domain}/{stem}")
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

    try:
        yaml_str = _generate_contract(kind, stem, domain, entity, engine, domain_path)
    except EmitterError as e:
        console.print(f"[red]Cannot emit a valid {kind} contract:[/red] {escape(str(e))}")
        sys.exit(1)
    if yaml_str is None:
        return

    # Preview
    console.print(f"\n[bold]{kind}/{domain}/{stem}[/bold]")
    console.print(Syntax(yaml_str, "yaml", theme="monokai", line_numbers=True))
    response = console.input("\n[bold]Write this contract? [Y/n] [/bold]").strip().lower()
    if response not in ("", "y", "yes"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    write_atomic(target, yaml_str)
    console.print(f"[green]wrote[/green] {target}")


def _generate_contract(kind, name, domain, entity_fqn, engine, domain_path: Path):
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
        return emit_page(name, domain, entity_fqn, _entity_columns(domain_path, entity_fqn))

    return None


def _entity_columns(domain_path: Path, entity_fqn: str) -> list[str]:
    """Read the target entity's own readable fields for the page's table view.

    An empty list is the safe answer: the frontend generator then falls back to
    the entity's first six readable fields. Guessing instead — this used to
    hard-code `["name"]` — produced a page that compiled and then failed
    generation with "entity has no such field" for every entity without a
    `name` column.
    """
    stem = entity_fqn.rsplit("/", 1)[-1]
    path = domain_path / "entities" / f"{stem}.contract.yaml"
    if not path.exists():
        console.print(
            f"[yellow]No contract at {path}; leaving the table columns for the "
            "generator to infer.[/yellow]"
        )
        return []

    try:
        contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        console.print(f"[yellow]Could not read {path}:[/yellow] {e}")
        return []

    if not isinstance(contract, dict):
        console.print(f"[yellow]{path} is not a contract mapping.[/yellow]")
        return []

    return page_columns((contract.get("spec") or {}).get("fields") or {})

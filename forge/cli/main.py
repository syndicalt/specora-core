"""Specora CLI — the command-line interface for the Forge engine.

Entry point for the `specora` command. Provides subcommands for:
  - forge validate  — Validate contracts against meta-schemas
  - forge compile   — Compile contracts into IR with summary
  - forge generate  — Compile + generate code for target platforms
  - forge graph     — Display the contract dependency graph
  - diff history    — Show contract change history
  - diff show       — Show details of a specific diff
  - init            — Scaffold a new domain

Usage:
    specora forge validate domains/library
    specora forge compile domains/library
    specora forge generate domains/library --target typescript --target postgres
    specora forge graph domains/library
    specora diff history entity/library/book
    specora init my_domain

Install:
    pip install -e .
    specora --help
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.tree import Tree

# Load .env file — don't override existing env vars (Docker sets them via env_file)
load_dotenv(override=False)

console = Console()


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging with rich handler."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False, show_path=False)],
    )


# =============================================================================
# Root CLI group
# =============================================================================


@click.group(invoke_without_command=True)
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Specora Core — Contract-Driven Development Engine.

    Build applications from declarative specifications.
    Run with no arguments to start the interactive REPL.
    """
    _setup_logging(verbose)

    # If no subcommand given, launch the interactive REPL
    if ctx.invoked_subcommand is None:
        _launch_repl()


# =============================================================================
# Forge commands
# =============================================================================


@cli.group()
def forge() -> None:
    """The compiler and code generation pipeline."""
    pass


@forge.command()
@click.argument("path", default="domains/", type=click.Path())
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text", help="Output format")
def validate(path: str, output_format: str) -> None:
    """Validate all contracts against their meta-schemas.

    Checks that every .contract.yaml file conforms to its kind's
    meta-schema. Reports all errors and warnings with human-readable
    messages and suggested fixes.
    """
    from forge.parser.loader import load_all_contracts
    from forge.parser.validator import validate_all
    from forge.error_display import format_errors_rich

    try:
        contracts = load_all_contracts(Path(path))
    except Exception as e:
        if output_format == "json":
            import json as json_mod
            click.echo(json_mod.dumps({"valid": False, "contract_count": 0, "errors": [{"fqn": "", "path": "", "message": str(e), "severity": "error"}], "warnings": []}))
        else:
            console.print(f"[red]Error loading contracts:[/red] {e}")
        sys.exit(1)

    errors = validate_all(contracts)
    real_errors = [e for e in errors if e.severity == "error"]
    warnings = [e for e in errors if e.severity != "error"]

    if output_format == "json":
        import json as json_mod
        result = {
            "valid": len(real_errors) == 0,
            "contract_count": len(contracts),
            "errors": [{"fqn": e.contract_fqn, "path": e.path, "message": e.message, "severity": e.severity} for e in real_errors],
            "warnings": [{"fqn": e.contract_fqn, "path": e.path, "message": e.message, "severity": e.severity} for e in warnings],
        }
        click.echo(json_mod.dumps(result))
        if real_errors:
            sys.exit(1)
        return

    if not errors:
        console.print(f"[green]All {len(contracts)} contracts are valid[/green]")
        return

    console.print(format_errors_rich(errors))

    if real_errors:
        sys.exit(1)


@forge.command()
@click.argument("path", default="domains/", type=click.Path())
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text", help="Output format")
def compile(path: str, output_format: str) -> None:
    """Compile contracts into IR.

    Runs the full pipeline: load -> validate -> resolve -> compile -> passes.
    Prints a summary of the compiled IR.
    """
    from forge.ir.compiler import Compiler, CompilationError

    try:
        compiler = Compiler(contract_root=Path(path))
        ir = compiler.compile()
    except CompilationError as e:
        if output_format == "json":
            import json as json_mod
            click.echo(json_mod.dumps({"success": False, "errors": [str(err) for err in e.errors]}))
        else:
            console.print(f"[red]Compilation failed:[/red]")
            for err in e.errors:
                console.print(f"  {err}")
        sys.exit(1)
    except Exception as e:
        if output_format == "json":
            import json as json_mod
            click.echo(json_mod.dumps({"success": False, "errors": [str(e)]}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if output_format == "json":
        import json as json_mod
        result = {
            "success": True,
            "summary": ir.summary(),
            "entities": len(ir.entities),
            "workflows": len(ir.workflows),
            "routes": len(ir.routes),
            "pages": len(ir.pages),
        }
        click.echo(json_mod.dumps(result))
        return

    console.print(f"[green]Compilation successful[/green]\n")
    console.print(ir.summary())


@forge.command()
@click.argument("path", default="domains/", type=click.Path())
@click.option("-t", "--target", multiple=True, default=["prod"],
              help="Target generators (prod = full stack, or specify individually)")
@click.option("-o", "--output", default="runtime/", type=click.Path(),
              help="Output directory for generated files")
def generate(path: str, target: tuple[str, ...], output: str) -> None:
    """Compile contracts and generate code.

    Runs the full compilation pipeline, then invokes target generators
    to produce code files in the output directory.
    """
    from forge.ir.compiler import Compiler, CompilationError
    from forge.targets.base import GeneratedFile

    # Compile
    try:
        compiler = Compiler(contract_root=Path(path))
        ir = compiler.compile()
    except CompilationError as e:
        console.print(f"[red]Compilation failed:[/red]")
        for err in e.errors:
            console.print(f"  {err}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(f"[green]Compiled[/green] — {ir.summary()}\n")

    # Discover generators
    generators = _get_generators(target)

    # Generate
    output_path = Path(output)
    total_files = 0

    for gen in generators:
        console.print(f"[bold]Generating: {gen.name()}[/bold]")
        try:
            files = gen.generate(ir)
            for f in files:
                file_path = output_path / f.path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                # Shell scripts must use LF line endings for Linux containers
                newline = "\n" if f.path.endswith(".sh") else None
                file_path.write_text(f.content, encoding="utf-8", newline=newline)
                console.print(f"  [green]wrote[/green] {f.path}")
                total_files += 1
        except Exception as e:
            console.print(f"  [red]Error in {gen.name()}:[/red] {e}")

    console.print(f"\n[green]Generated {total_files} files[/green] in {output_path}")

    # Auto-create .env from .env.example if it doesn't exist
    env_example = output_path / ".env.example"
    env_file = output_path / ".env"
    if env_example.exists() and not env_file.exists():
        import shutil
        shutil.copy2(env_example, env_file)
        console.print(f"[cyan]Created .env from .env.example[/cyan] — edit secrets before deploying")


@forge.command()
@click.argument("path", default="domains/", type=click.Path())
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text", help="Output format")
def graph(path: str, output_format: str) -> None:
    """Display the contract dependency graph."""
    from forge.parser.graph import build_dependency_graph
    from forge.parser.loader import load_all_contracts

    try:
        contracts = load_all_contracts(Path(path))
    except Exception as e:
        if output_format == "json":
            import json as json_mod
            click.echo(json_mod.dumps({"nodes": [], "count": 0, "error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    dep_graph = build_dependency_graph(contracts)

    if output_format == "json":
        import json as json_mod
        nodes = []
        for node in sorted(dep_graph.nodes.values(), key=lambda n: n.fqn):
            nodes.append({
                "fqn": node.fqn,
                "kind": node.kind,
                "dependencies": dep_graph.dependencies_of(node.fqn),
                "dependents": dep_graph.dependents_of(node.fqn),
            })
        click.echo(json_mod.dumps({"nodes": nodes, "count": len(nodes)}))
        return

    # Build a rich Tree
    tree = Tree(f"[bold]Contract Graph[/bold] ({len(dep_graph.nodes)} contracts)")

    # Group by kind
    by_kind: dict[str, list] = {}
    for node in dep_graph.nodes.values():
        by_kind.setdefault(node.kind, []).append(node)

    for kind, nodes in sorted(by_kind.items()):
        kind_branch = tree.add(f"[bold cyan]{kind}[/bold cyan] ({len(nodes)})")
        for node in sorted(nodes, key=lambda n: n.fqn):
            deps = dep_graph.dependencies_of(node.fqn)
            dependents = dep_graph.dependents_of(node.fqn)
            label = f"[green]{node.fqn}[/green]"
            if deps:
                label += f" [dim]requires: {', '.join(deps)}[/dim]"
            if dependents:
                label += f" [dim yellow]used by: {', '.join(dependents)}[/dim yellow]"
            kind_branch.add(label)

    console.print(tree)


# =============================================================================
# Diff commands
# =============================================================================


@cli.group()
def diff() -> None:
    """Contract diff tracking."""
    pass


@diff.command()
@click.argument("contract_fqn")
def history(contract_fqn: str) -> None:
    """Show the change history for a contract."""
    from forge.diff.store import DiffStore

    store = DiffStore(root=Path(".forge/diffs"))
    diffs = store.get_history(contract_fqn)

    if not diffs:
        console.print(f"No change history for [bold]{contract_fqn}[/bold]")
        return

    table = Table(title=f"Change History: {contract_fqn}")
    table.add_column("Date", style="cyan")
    table.add_column("Origin", style="green")
    table.add_column("Reason")
    table.add_column("Changes", justify="right")

    for d in reversed(diffs):
        table.add_row(
            d.timestamp.strftime("%Y-%m-%d %H:%M"),
            f"{d.origin.value} ({d.origin_detail})" if d.origin_detail else d.origin.value,
            d.reason[:60],
            str(len(d.changes)),
        )

    console.print(table)


@diff.command()
@click.argument("diff_id")
def show(diff_id: str) -> None:
    """Show details of a specific diff."""
    from forge.diff.store import DiffStore

    store = DiffStore(root=Path(".forge/diffs"))
    d = store.get_diff(diff_id)

    if not d:
        console.print(f"[red]Diff not found:[/red] {diff_id}")
        sys.exit(1)

    console.print(f"[bold]Diff: {d.id}[/bold]")
    console.print(f"Contract: {d.contract_fqn}")
    console.print(f"Date: {d.timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
    console.print(f"Origin: {d.origin.value} ({d.origin_detail})")
    console.print(f"Reason: {d.reason}")
    console.print()
    console.print(f"[bold]Changes ({len(d.changes)}):[/bold]")

    for change in d.changes:
        if change.change_type == "added":
            console.print(f"  [green]+[/green] {change.path}: {change.new_value}")
        elif change.change_type == "removed":
            console.print(f"  [red]-[/red] {change.path}: {change.old_value}")
        else:
            console.print(f"  [yellow]~[/yellow] {change.path}: {change.old_value} -> {change.new_value}")


# =============================================================================
# Init command
# =============================================================================


@cli.command()
@click.argument("domain")
@click.option("--input", "-i", "input_dir", default="domains/", type=click.Path(),
              help="Base directory for contracts (default: domains/)")
def init(domain: str, input_dir: str) -> None:
    """Scaffold a new domain with starter contracts.

    Creates the directory structure and a starter entity contract.
    """
    domain_path = Path(input_dir) / domain

    if domain_path.exists():
        console.print(f"[red]Domain directory already exists:[/red] {domain_path}")
        sys.exit(1)

    # Create directories
    for subdir in ["entities", "workflows", "pages", "routes", "agents"]:
        (domain_path / subdir).mkdir(parents=True, exist_ok=True)

    # Create a starter entity
    starter = f"""apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: example
  domain: {domain}
  description: "A starter entity — replace with your own"
  tags: [starter]

requires:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable

spec:
  fields:
    name:
      type: string
      required: true
      description: "The name of this record"
      constraints:
        maxLength: 200
    description:
      type: text
      description: "Detailed description"
    active:
      type: boolean
      default: true
      description: "Whether this record is active"
"""

    (domain_path / "entities" / "example.contract.yaml").write_text(starter, encoding="utf-8")

    console.print(f"[green]Domain '{domain}' initialized at {domain_path}[/green]")
    console.print()
    console.print("Created:")
    console.print(f"  {domain_path}/entities/example.contract.yaml")
    console.print()
    console.print("Next steps:")
    console.print(f"  1. Edit entities in {domain_path}/entities/")
    console.print(f"  2. Add workflows in {domain_path}/workflows/")
    console.print(f"  3. Add pages in {domain_path}/pages/")
    console.print(f"  4. Run: specora forge validate {domain_path}")
    console.print(f"  5. Run: specora forge generate {domain_path}")


# =============================================================================
# Factory commands
# =============================================================================


@cli.group()
def factory() -> None:
    """The Factory — conversational contract authoring (LLM-powered)."""
    pass


# Import and register factory commands
from factory.cli.new import factory_new
from factory.cli.add import factory_add
from factory.cli.explain import factory_explain
from factory.cli.refine import factory_refine
from factory.cli.chat import factory_chat
from factory.cli.visualize import factory_visualize
from factory.cli.migrate import factory_migrate
factory.add_command(factory_new, "new")
factory.add_command(factory_add, "add")
factory.add_command(factory_explain, "explain")
factory.add_command(factory_refine, "refine")
factory.add_command(factory_chat, "chat")
factory.add_command(factory_visualize, "visualize")
factory.add_command(factory_migrate, "migrate")


# =============================================================================
# Healer commands
# =============================================================================

# Import and register healer commands
from healer.cli.commands import healer as healer_group
cli.add_command(healer_group, "healer")


# =============================================================================
# Extractor commands
# =============================================================================

# Import and register extractor commands
from extractor.cli.commands import extract as extract_cmd
cli.add_command(extract_cmd, "extract")

# Import and register project scaffolder
from forge.cli.init_project import init_project
cli.add_command(init_project, "init-project")


# =============================================================================
# Generator registry
# =============================================================================


def _get_generators(target_names: tuple[str, ...]) -> list:
    """Look up generators by name.

    Args:
        target_names: Generator names (e.g., ("typescript", "fastapi")).

    Returns:
        List of instantiated generator objects.
    """
    from forge.targets.typescript.gen_types import TypeScriptGenerator
    from forge.targets.fastapi.gen_routes import FastAPIGenerator
    from forge.targets.postgres.gen_ddl import PostgresGenerator
    from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator, DockerGenerator, TestSuiteGenerator
    from forge.targets.migrations.generator import MigrationGenerator
    from forge.targets.nextjs.generator import NextJSGenerator

    registry = {
        "typescript": TypeScriptGenerator,
        "fastapi": FastAPIGenerator,
        "postgres": PostgresGenerator,
        "fastapi-prod": FastAPIProductionGenerator,
        "docker": DockerGenerator,
        "tests": TestSuiteGenerator,
        "migrations": MigrationGenerator,
        "nextjs": NextJSGenerator,
    }

    # Aliases — expand shorthand names into multiple generators
    aliases = {
        "prod": ["fastapi-prod", "postgres", "docker", "tests", "nextjs", "migrations"],
    }

    # Expand aliases
    expanded = []
    for name in target_names:
        if name in aliases:
            expanded.extend(aliases[name])
        else:
            expanded.append(name)

    generators = []
    seen = set()
    for name in expanded:
        if name in seen:
            continue
        seen.add(name)
        cls = registry.get(name)
        if cls:
            generators.append(cls())
        else:
            console.print(f"[yellow]Unknown target '{name}', skipping[/yellow]")
            console.print(f"  Available: {', '.join(list(registry.keys()) + list(aliases.keys()))}")

    return generators


def _launch_repl() -> None:
    """Launch the interactive REPL."""
    from forge.cli.repl import main as repl_main
    repl_main()


if __name__ == "__main__":
    cli()

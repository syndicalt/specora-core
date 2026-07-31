"""specora factory visualize — Mermaid diagram generation."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.syntax import Syntax

from forge.parser.loader import load_all_contracts

console = Console()

# Mermaid node and edge names are bare tokens. Field names in particular have
# no pattern in entity.meta.yaml, so anything outside this set has to be
# folded away or the whole diagram fails to render.
_IDENT_UNSAFE = re.compile(r"[^A-Za-z0-9_]+")


def _ident(value: object) -> str:
    """Fold an arbitrary contract value into a Mermaid-safe token."""
    cleaned = _IDENT_UNSAFE.sub("_", str(value)).strip("_")
    return cleaned or "unnamed"


@click.command("visualize")
@click.argument("path", default="domains/", type=click.Path(exists=True))
@click.option(
    "--type",
    "diagram_type",
    type=click.Choice(["er", "state", "deps"]),
    default="er",
    help="Diagram type",
)
@click.option("--output", "-o", default="", help="Save to file instead of printing")
def factory_visualize(path: str, diagram_type: str, output: str) -> None:
    """Generate Mermaid diagrams for contracts."""
    try:
        contracts = load_all_contracts(Path(path))
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if not contracts:
        console.print("[yellow]No contracts found.[/yellow]")
        return

    if diagram_type == "er":
        mermaid = _generate_er_diagram(contracts)
    elif diagram_type == "state":
        mermaid = _generate_state_diagrams(contracts)
    elif diagram_type == "deps":
        mermaid = _generate_deps_diagram(contracts)
    else:
        mermaid = ""

    if not mermaid:
        console.print(f"[yellow]No {diagram_type} data found to visualize.[/yellow]")
        return

    if output:
        Path(output).write_text(mermaid, encoding="utf-8")
        console.print(f"[green]Saved to {output}[/green]")
    else:
        console.print(Syntax(mermaid, "mermaid", theme="monokai"))
        console.print("\n[dim]Paste into https://mermaid.live to render[/dim]")


def _generate_er_diagram(contracts: dict[str, dict]) -> str:
    """Generate an entity-relationship diagram."""
    entities = {fqn: c for fqn, c in contracts.items() if c.get("kind") == "Entity"}
    if not entities:
        return ""

    lines = ["erDiagram"]

    for _fqn, contract in sorted(entities.items()):
        name = _ident((contract.get("metadata") or {}).get("name", "?"))
        fields = (contract.get("spec") or {}).get("fields") or {}

        # Entity block
        lines.append(f"    {name} {{")
        for field_name, field_def in fields.items():
            if not isinstance(field_def, dict):
                continue
            ftype = _ident(field_def.get("type", "string"))
            required = "PK" if field_name == "id" else ("FK" if field_def.get("references") else "")
            lines.append(f"        {ftype} {_ident(field_name)} {required}".rstrip())
        lines.append("    }")

        # Relationships from references
        for field_name, field_def in fields.items():
            if not isinstance(field_def, dict):
                continue
            ref = field_def.get("references") or {}
            if isinstance(ref, dict) and "entity" in ref:
                target_name = _ident(str(ref["entity"]).rsplit("/", 1)[-1])
                edge_label = _ident(ref.get("graph_edge", field_name))
                lines.append(f"    {name} ||--o{{ {target_name} : {edge_label}")

    return "\n".join(lines)


def _generate_state_diagrams(contracts: dict[str, dict]) -> str:
    """Generate state machine diagrams for workflows."""
    workflows = {fqn: c for fqn, c in contracts.items() if c.get("kind") == "Workflow"}
    if not workflows:
        return ""

    diagrams = []
    for _fqn, contract in sorted(workflows.items()):
        name = contract.get("metadata", {}).get("name", "?")
        spec = contract.get("spec", {})
        initial = spec.get("initial", "")
        transitions = spec.get("transitions", [])

        lines = ["---", f"title: {name}", "---", "stateDiagram-v2"]

        if initial:
            lines.append(f"    [*] --> {initial}")

        # workflow.meta.yaml declares transitions as a map of source state to
        # a list of target states; nothing else is a valid contract.
        if isinstance(transitions, dict):
            for src, targets in transitions.items():
                for dst in targets if isinstance(targets, list) else [targets]:
                    lines.append(f"    {_ident(src)} --> {_ident(dst)}")
        elif transitions:
            console.print(
                f"[yellow]{name}: spec.transitions is a "
                f"{type(transitions).__name__}, not a mapping — skipping its edges.[/yellow]"
            )

        diagrams.append("\n".join(lines))

    return "\n\n".join(diagrams)


def _generate_deps_diagram(contracts: dict[str, dict]) -> str:
    """Generate a dependency graph diagram."""
    lines = ["graph TD"]

    for fqn, contract in sorted(contracts.items()):
        short = _ident(fqn.rsplit("/", 1)[-1])
        kind = contract.get("kind", "?")
        shape = {
            "Entity": f"[{short}]",
            "Workflow": f"(({short}))",
            "Route": f"[/{short}/]",
            "Page": f">{short}]",
        }.get(kind, f"[{short}]")
        lines.append(f"    {short}{shape}")

        for req in contract.get("requires") or []:
            lines.append(f"    {short} --> {_ident(str(req).rsplit('/', 1)[-1])}")

    return "\n".join(lines)

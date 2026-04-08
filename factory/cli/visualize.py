"""specora factory visualize — Mermaid diagram generation."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.syntax import Syntax

from forge.parser.loader import load_all_contracts

console = Console()


@click.command("visualize")
@click.argument("path", default="domains/", type=click.Path(exists=True))
@click.option("--type", "diagram_type", type=click.Choice(["er", "state", "deps"]), default="er", help="Diagram type")
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
        console.print(f"\n[dim]Paste into https://mermaid.live to render[/dim]")


def _generate_er_diagram(contracts: dict[str, dict]) -> str:
    """Generate an entity-relationship diagram."""
    entities = {fqn: c for fqn, c in contracts.items() if c.get("kind") == "Entity"}
    if not entities:
        return ""

    lines = ["erDiagram"]

    for fqn, contract in sorted(entities.items()):
        name = contract.get("metadata", {}).get("name", "?")
        fields = contract.get("spec", {}).get("fields", {})

        # Entity block
        lines.append(f"    {name} {{")
        for field_name, field_def in fields.items():
            ftype = field_def.get("type", "string")
            required = "PK" if field_name == "id" else ("FK" if field_def.get("references") else "")
            lines.append(f"        {ftype} {field_name} {required}".rstrip())
        lines.append("    }")

        # Relationships from references
        for field_name, field_def in fields.items():
            ref = field_def.get("references", {})
            if ref and "entity" in ref:
                target_fqn = ref["entity"]
                # Extract target name from FQN
                target_name = target_fqn.split("/")[-1] if "/" in target_fqn else target_fqn
                edge_label = ref.get("graph_edge", field_name)
                lines.append(f"    {name} ||--o{{ {target_name} : {edge_label}")

    return "\n".join(lines)


def _generate_state_diagrams(contracts: dict[str, dict]) -> str:
    """Generate state machine diagrams for workflows."""
    workflows = {fqn: c for fqn, c in contracts.items() if c.get("kind") == "Workflow"}
    if not workflows:
        return ""

    diagrams = []
    for fqn, contract in sorted(workflows.items()):
        name = contract.get("metadata", {}).get("name", "?")
        spec = contract.get("spec", {})
        initial = spec.get("initial", "")
        transitions = spec.get("transitions", [])

        lines = [f"---", f"title: {name}", f"---", "stateDiagram-v2"]

        if initial:
            lines.append(f"    [*] --> {initial}")

        if isinstance(transitions, dict):
            # Dict-of-lists format: { available: [checked_out, reserved], ... }
            for src, targets in transitions.items():
                if isinstance(targets, list):
                    for dst in targets:
                        lines.append(f"    {src} --> {dst}")
                elif isinstance(targets, str):
                    lines.append(f"    {src} --> {targets}")
        elif isinstance(transitions, list):
            # List-of-dicts format: [{ from: x, to: y, label: z }, ...]
            for t in transitions:
                if isinstance(t, dict):
                    src = t.get("from", "?")
                    dst = t.get("to", "?")
                    label = t.get("label", "")
                    if label:
                        lines.append(f"    {src} --> {dst} : {label}")
                    else:
                        lines.append(f"    {src} --> {dst}")

        diagrams.append("\n".join(lines))

    return "\n\n".join(diagrams)


def _generate_deps_diagram(contracts: dict[str, dict]) -> str:
    """Generate a dependency graph diagram."""
    lines = ["graph TD"]

    for fqn, contract in sorted(contracts.items()):
        short = fqn.split("/")[-1]
        kind = contract.get("kind", "?")
        shape = {
            "Entity": f"[{short}]",
            "Workflow": f"(({short}))",
            "Route": f"[/{short}/]",
            "Page": f">{short}]",
        }.get(kind, f"[{short}]")
        lines.append(f"    {short.replace('-', '_')}{shape}")

        for req in contract.get("requires", []):
            req_short = req.split("/")[-1]
            lines.append(f"    {short.replace('-', '_')} --> {req_short.replace('-', '_')}")

    return "\n".join(lines)

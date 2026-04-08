# extractor/reporter.py
"""Rich-formatted analysis report with accept/edit/skip per entity."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from extractor.models import AnalysisReport, Confidence, ExtractedEntity

console = Console()


def display_report(report: AnalysisReport) -> list[ExtractedEntity]:
    """Display the analysis report and let the user accept/skip each entity.

    Returns the list of accepted entities.
    """
    console.print()
    console.print(Rule("[bold magenta]Extraction Report[/bold magenta]", style="magenta"))
    console.print()

    # Summary table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Domain", f"[cyan]{report.domain}[/cyan]")
    table.add_row("Files scanned", str(report.files_scanned))
    table.add_row("Files analyzed", str(report.files_analyzed))
    table.add_row("Entities found", f"[green]{len(report.entities)}[/green]")
    table.add_row("Routes found", str(len(report.routes)))
    table.add_row("Workflows detected", str(len(report.workflows)))
    console.print(table)
    console.print()

    if not report.entities:
        console.print("  [yellow]No entities found.[/yellow]")
        return []

    console.print(Rule("[bold]Review Entities[/bold]", style="dim"))
    console.print()

    accepted: list[ExtractedEntity] = []

    for i, entity in enumerate(report.entities, 1):
        confidence_color = {"high": "green", "medium": "yellow", "low": "red"}.get(entity.confidence.value, "white")

        # Entity header
        console.print(f"  [bold]{i}/{len(report.entities)}[/bold]  [bold cyan]{entity.name}[/bold cyan]  [{confidence_color}]{entity.confidence.value} confidence[/{confidence_color}]")
        if entity.description:
            console.print(f"  [dim]{entity.description}[/dim]")
        console.print(f"  [dim]Source: {entity.source_file}[/dim]")

        # Fields table
        if entity.fields:
            ft = Table(show_header=True, box=None, padding=(0, 1), show_edge=False)
            ft.add_column("Field", style="cyan", min_width=16)
            ft.add_column("Type", min_width=10)
            ft.add_column("Req", justify="center", min_width=3)
            ft.add_column("Details", style="dim")

            for f in entity.fields:
                req = "✓" if f.required else ""
                details = []
                if f.enum_values:
                    details.append(f"enum: {', '.join(f.enum_values[:4])}")
                if f.reference_entity:
                    details.append(f"→ {f.reference_entity}")
                ft.add_row(f.name, f.type, req, " | ".join(details))

            console.print(ft)

        # State machine
        if entity.state_field:
            states = " → ".join(entity.state_values)
            console.print(f"  [dim]State machine: {entity.state_field} ({states})[/dim]")

        console.print()

        # Accept/Skip
        try:
            response = console.input("  [bold][A]ccept / [S]kip? [/bold]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n  [dim]Cancelled.[/dim]")
            return accepted

        if response in ("", "a", "accept", "y", "yes"):
            accepted.append(entity)
            console.print("  [green]✓ Accepted[/green]")
        else:
            console.print("  [yellow]— Skipped[/yellow]")

        console.print()

    console.print(Rule(style="dim"))
    console.print(f"  [bold]{len(accepted)}/{len(report.entities)} entities accepted[/bold]")
    console.print()

    return accepted

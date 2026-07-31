"""specora extract — reverse-engineer codebases into contracts."""

from __future__ import annotations

import time
from pathlib import Path

import click
from rich.console import Console
from rich.rule import Rule

from extractor.emitter import EmissionError, emit_contracts
from extractor.models import safe_contract_name
from extractor.reporter import display_report
from extractor.scanner import ScanLimits
from extractor.synthesizer import synthesize

console = Console()


@click.command("extract")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--domain", "-d", default="", help="Domain name (auto-inferred from directory name if omitted)"
)
@click.option("--output", "-o", default="domains/", help="Output base directory")
@click.option(
    "--max-file-kb",
    default=ScanLimits().max_file_bytes // 1024,
    show_default=True,
    help="Skip source files larger than this.",
)
@click.option(
    "--max-files",
    default=ScanLimits().max_files,
    show_default=True,
    help="Stop scanning after this many source files.",
)
def extract(path: str, domain: str, output: str, max_file_kb: int, max_files: int) -> None:
    """Reverse-engineer a codebase into Specora contracts.

    Analyzes Python and TypeScript source files, extracts entities,
    routes, and workflows, then emits contract YAML files.
    """
    source_path = Path(path)

    # Auto-infer domain name
    domain = safe_contract_name(domain or source_path.name, fallback="extracted")
    limits = ScanLimits(max_file_bytes=max_file_kb * 1024, max_files=max_files)

    console.print()
    console.print(Rule(f"[bold magenta]Extracting: {source_path}[/bold magenta]", style="magenta"))
    console.print(f"  [dim]Domain: {domain}[/dim]")
    console.print()

    # Run the 4-pass pipeline
    start = time.time()
    with console.status("[magenta]Scanning files…[/magenta]", spinner="dots"):
        report = synthesize(source_path, domain, limits=limits)
    elapsed = time.time() - start

    console.print(
        f"  [dim]Scanned {report.files_scanned} files, "
        f"analyzed {report.files_analyzed} ({elapsed:.1f}s)[/dim]"
    )
    console.print()

    if not report.entities:
        console.print(
            "  [yellow]No entities found. "
            "The codebase may not contain recognizable models.[/yellow]"
        )
        return

    # Display report and get user approvals
    accepted = display_report(report)

    if not accepted:
        console.print("  [yellow]No entities accepted. No contracts written.[/yellow]")
        return

    # Confirm write
    output_dir = Path(output) / domain
    console.print(
        f"  Writing {len(accepted)} entities (+ routes + pages) to [cyan]{output_dir}[/cyan]"
    )
    try:
        response = console.input("  [bold]Proceed? [Y/n] [/bold]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n  [dim]Cancelled.[/dim]")
        return

    if response not in ("", "y", "yes"):
        console.print("  [yellow]Cancelled.[/yellow]")
        return

    # Emit
    try:
        written = emit_contracts(report, output_dir, accepted_entities=accepted)
    except EmissionError as e:
        console.print(f"  [bold red]Nothing written:[/bold red] {e}")
        raise SystemExit(1) from e

    console.print()
    for p in written:
        console.print(f"  [green]✓[/green] {p}")

    console.print()
    console.print(Rule(style="dim"))
    console.print(f"  [bold green]Wrote {len(written)} contracts to {output_dir}[/bold green]")
    console.print()
    console.print("  [dim]Next steps:[/dim]")
    console.print(f"  [dim]  spc forge validate {output_dir}[/dim]")
    console.print(f"  [dim]  spc forge generate {output_dir}[/dim]")
    console.print()

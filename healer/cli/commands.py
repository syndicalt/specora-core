"""Healer CLI commands — fix, status, tickets, show, approve, reject, history."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from healer.models import Priority, TicketSource, TicketStatus
from healer.queue import HealerQueue
from healer.pipeline import HealerPipeline

console = Console()


def _default_queue() -> HealerQueue:
    """Create a HealerQueue with the default database path."""
    return HealerQueue(db_path=Path(".forge/healer/healer.db"))


def _default_pipeline(queue: HealerQueue) -> HealerPipeline:
    """Create a HealerPipeline with default paths."""
    return HealerPipeline(queue=queue)


def _resolve_ticket_id(queue: HealerQueue, short_id: str) -> str | None:
    """Resolve a short ticket ID prefix to a full ID.

    Supports both full UUIDs and prefix matches (minimum 4 chars).
    Returns None if no match or ambiguous.
    """
    # Try exact match first
    ticket = queue.get_ticket(short_id)
    if ticket:
        return short_id

    # Prefix match
    tickets = queue.list_tickets()
    matches = [t for t in tickets if t.id.startswith(short_id)]
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        console.print(f"[red]Ambiguous ID prefix '{short_id}' — matches {len(matches)} tickets:[/red]")
        for t in matches:
            console.print(f"  {t.id[:8]}  {t.status.value}  {t.raw_error[:40]}")
        return None
    return None


@click.group()
def healer() -> None:
    """The Healer — self-healing contract pipeline."""
    pass


@healer.command()
@click.argument("path", default="domains/", type=click.Path())
def fix(path: str) -> None:
    """Load contracts, validate, create tickets for errors, and process fixes.

    Scans the contract directory, validates all contracts, creates healer
    tickets for any validation errors, and processes them through the pipeline.
    """
    from forge.parser.loader import load_all_contracts
    from forge.parser.validator import validate_all

    queue = _default_queue()
    pipeline = _default_pipeline(queue)

    # Load and validate
    try:
        contracts = load_all_contracts(Path(path))
    except Exception as e:
        console.print(f"[red]Error loading contracts:[/red] {e}")
        sys.exit(1)

    console.print(f"Loaded [bold]{len(contracts)}[/bold] contracts from {path}")

    errors = validate_all(contracts)
    if not errors:
        console.print("[green]All contracts are valid — nothing to heal[/green]")
        return

    real_errors = [e for e in errors if e.severity == "error"]
    warnings = [e for e in errors if e.severity != "error"]

    console.print(
        f"Found [red]{len(real_errors)} errors[/red] and "
        f"[yellow]{len(warnings)} warnings[/yellow]"
    )

    # Create tickets for real errors
    from healer.models import HealerTicket

    created = 0
    for err in real_errors:
        ticket = HealerTicket(
            source=TicketSource.VALIDATION,
            raw_error=err.message,
            contract_fqn=err.contract_fqn,
            context={"path": err.path, "source_path": err.source_path},
        )
        queue.enqueue(ticket)
        created += 1

    console.print(f"Created [bold]{created}[/bold] healer tickets")

    # Process all queued tickets
    processed = 0
    while pipeline.process_next():
        processed += 1

    console.print(f"Processed [bold]{processed}[/bold] tickets")

    # Show summary
    stats = queue.stats()
    by_status = stats["by_status"]
    applied = by_status.get("applied", 0)
    proposed = by_status.get("proposed", 0)
    failed = by_status.get("failed", 0)

    console.print()
    if applied:
        console.print(f"  [green]{applied} fixes applied automatically[/green]")
    if proposed:
        console.print(f"  [yellow]{proposed} fixes awaiting approval[/yellow] (run: specora healer tickets)")
    if failed:
        console.print(f"  [red]{failed} tickets failed[/red]")


@healer.command()
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text", help="Output format")
def status(output_format: str) -> None:
    """Show queue statistics."""
    queue = _default_queue()
    stats = queue.stats()
    by_status = stats["by_status"]

    if output_format == "json":
        import json as json_mod
        click.echo(json_mod.dumps({"by_status": by_status, "total": stats["total"]}))
        return

    table = Table(title="Healer Queue Status")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")

    status_styles = {
        "queued": "cyan",
        "analyzing": "blue",
        "proposed": "yellow",
        "approved": "green",
        "applied": "green",
        "failed": "red",
        "rejected": "dim",
    }

    for s in ["queued", "analyzing", "proposed", "approved", "applied", "failed", "rejected"]:
        count = by_status.get(s, 0)
        style = status_styles.get(s, "")
        table.add_row(f"[{style}]{s}[/{style}]", str(count))

    table.add_section()
    table.add_row("[bold]Total[/bold]", str(stats["total"]))

    console.print(table)


@healer.command()
@click.option("--status", "status_filter", type=click.Choice(
    ["queued", "analyzing", "proposed", "approved", "applied", "failed", "rejected"],
    case_sensitive=False,
), default=None, help="Filter by ticket status")
@click.option("--priority", "priority_filter", type=click.Choice(
    ["critical", "high", "medium", "low"], case_sensitive=False,
), default=None, help="Filter by priority")
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text", help="Output format")
def tickets(status_filter: str | None, priority_filter: str | None, output_format: str) -> None:
    """List healer tickets with optional filters."""
    queue = _default_queue()

    status_enum = TicketStatus(status_filter) if status_filter else None
    priority_enum = Priority(priority_filter) if priority_filter else None

    ticket_list = queue.list_tickets(status=status_enum, priority=priority_enum)

    if output_format == "json":
        import json as json_mod
        result = [
            {
                "id": t.id,
                "status": t.status.value,
                "priority": t.priority.value,
                "tier": t.tier,
                "contract_fqn": t.contract_fqn or "",
                "error": t.raw_error,
            }
            for t in ticket_list
        ]
        click.echo(json_mod.dumps(result))
        return

    if not ticket_list:
        console.print("[dim]No tickets found[/dim]")
        return

    table = Table(title=f"Healer Tickets ({len(ticket_list)})")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Priority")
    table.add_column("Tier", justify="right")
    table.add_column("Contract", style="green")
    table.add_column("Error")

    status_styles = {
        "queued": "cyan",
        "analyzing": "blue",
        "proposed": "yellow",
        "approved": "green",
        "applied": "green",
        "failed": "red",
        "rejected": "dim",
    }
    priority_styles = {
        "critical": "red bold",
        "high": "red",
        "medium": "yellow",
        "low": "dim",
    }

    for t in ticket_list:
        s_style = status_styles.get(t.status.value, "")
        p_style = priority_styles.get(t.priority.value, "")
        table.add_row(
            t.id[:8],
            f"[{s_style}]{t.status.value}[/{s_style}]",
            f"[{p_style}]{t.priority.value}[/{p_style}]",
            str(t.tier),
            t.contract_fqn or "",
            t.raw_error[:40],
        )

    console.print(table)


@healer.command()
@click.argument("ticket_id")
def show(ticket_id: str) -> None:
    """Show detailed information about a ticket."""
    queue = _default_queue()

    full_id = _resolve_ticket_id(queue, ticket_id)
    if not full_id:
        console.print(f"[red]Ticket not found:[/red] {ticket_id}")
        sys.exit(1)

    ticket = queue.get_ticket(full_id)
    if not ticket:
        console.print(f"[red]Ticket not found:[/red] {ticket_id}")
        sys.exit(1)

    console.print(f"[bold]Ticket: {ticket.id}[/bold]")
    console.print(f"  Status:    {ticket.status.value}")
    console.print(f"  Priority:  {ticket.priority.value}")
    console.print(f"  Tier:      {ticket.tier}")
    console.print(f"  Source:    {ticket.source.value}")
    console.print(f"  Contract:  {ticket.contract_fqn or 'N/A'}")
    console.print(f"  Error:     {ticket.error_type or 'N/A'}")
    console.print(f"  Message:   {ticket.raw_error}")
    console.print(f"  Created:   {ticket.created_at.strftime('%Y-%m-%d %H:%M UTC')}")

    if ticket.resolved_at:
        console.print(f"  Resolved:  {ticket.resolved_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if ticket.resolution_note:
        console.print(f"  Note:      {ticket.resolution_note}")

    if ticket.context:
        console.print()
        console.print("[bold]Context:[/bold]")
        for k, v in ticket.context.items():
            console.print(f"  {k}: {v}")

    if ticket.proposal:
        console.print()
        console.print("[bold]Proposal:[/bold]")
        console.print(f"  Method:     {ticket.proposal.method}")
        console.print(f"  Confidence: {ticket.proposal.confidence:.0%}")
        console.print(f"  Explanation: {ticket.proposal.explanation}")
        if ticket.proposal.changes:
            console.print(f"  Changes:    {len(ticket.proposal.changes)}")


@healer.command()
@click.argument("ticket_id")
def approve(ticket_id: str) -> None:
    """Approve a proposed fix and apply it."""
    queue = _default_queue()
    pipeline = _default_pipeline(queue)

    full_id = _resolve_ticket_id(queue, ticket_id)
    if not full_id:
        console.print(f"[red]Ticket not found:[/red] {ticket_id}")
        sys.exit(1)

    success = pipeline.approve_ticket(full_id)
    if success:
        console.print(f"[green]Approved and applied:[/green] {full_id[:8]}")
    else:
        ticket = queue.get_ticket(full_id)
        if ticket and ticket.status != TicketStatus.PROPOSED:
            console.print(
                f"[red]Cannot approve:[/red] ticket is '{ticket.status.value}' "
                f"(must be 'proposed')"
            )
        else:
            console.print(f"[red]Failed to approve ticket[/red]")
        sys.exit(1)


@healer.command()
@click.argument("ticket_id")
@click.option("--reason", "-r", default="", help="Reason for rejection")
def reject(ticket_id: str, reason: str) -> None:
    """Reject a proposed fix."""
    queue = _default_queue()
    pipeline = _default_pipeline(queue)

    full_id = _resolve_ticket_id(queue, ticket_id)
    if not full_id:
        console.print(f"[red]Ticket not found:[/red] {ticket_id}")
        sys.exit(1)

    success = pipeline.reject_ticket(full_id, reason=reason)
    if success:
        console.print(f"[yellow]Rejected:[/yellow] {full_id[:8]}")
        if reason:
            console.print(f"  Reason: {reason}")
    else:
        ticket = queue.get_ticket(full_id)
        if ticket and ticket.status != TicketStatus.PROPOSED:
            console.print(
                f"[red]Cannot reject:[/red] ticket is '{ticket.status.value}' "
                f"(must be 'proposed')"
            )
        else:
            console.print(f"[red]Failed to reject ticket[/red]")
        sys.exit(1)


@healer.command()
@click.option("--port", default=8083, help="Port to serve on")
@click.option("--host", default="0.0.0.0", help="Host to bind to")
def serve(port: int, host: str) -> None:
    """Start the Healer HTTP service."""
    import uvicorn
    from healer.api.server import app
    console.print(f"[bold]Starting Healer service on {host}:{port}[/bold]")
    uvicorn.run(app, host=host, port=port)


@healer.command()
def history() -> None:
    """Show applied healer fixes from the diff store."""
    from forge.diff.store import DiffStore
    from forge.diff.models import DiffOrigin

    store = DiffStore(root=Path(".forge/diffs"))
    diffs = store.list_diffs(origin=DiffOrigin.HEALER)

    if not diffs:
        console.print("[dim]No healer diffs recorded[/dim]")
        return

    table = Table(title=f"Healer Fix History ({len(diffs)})")
    table.add_column("Date", style="cyan")
    table.add_column("Contract", style="green")
    table.add_column("Reason")
    table.add_column("Changes", justify="right")

    for d in diffs:
        table.add_row(
            d.timestamp.strftime("%Y-%m-%d %H:%M"),
            d.contract_fqn,
            d.reason[:50],
            str(len(d.changes)),
        )

    console.print(table)

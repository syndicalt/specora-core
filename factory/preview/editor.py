"""Editor preview — write contracts to temp dir, open in $EDITOR.

After the Factory generates contracts, it writes them to a temporary
directory and opens them in the user's preferred editor. The user
reviews, makes any edits, then returns to the Factory which reads
back the (possibly modified) files.

If $EDITOR is not set, falls back to terminal preview using Rich.

Usage:
    from factory.preview.editor import preview_contracts

    accepted, files = preview_contracts({
        "entities/patient.contract.yaml": yaml_content,
        "workflows/patient_lifecycle.contract.yaml": yaml_content,
    })
    if accepted:
        for path, content in files.items():
            # Write to actual domain directory
"""

from __future__ import annotations

import os
import logging
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax

logger = logging.getLogger(__name__)
console = Console()


def preview_contracts(
    contracts: dict[str, str],
    domain: str = "",
) -> tuple[bool, dict[str, str]]:
    """Preview generated contracts in $EDITOR or terminal.

    Writes contracts to a temp directory, opens the editor, then reads
    them back. The user can modify contracts in the editor before accepting.

    Args:
        contracts: Map of relative path -> YAML content.
        domain: Domain name (for display).

    Returns:
        Tuple of (accepted: bool, files: dict[path, content]).
        If accepted, files contains the (possibly edited) content.
    """
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))

    if editor:
        return _preview_in_editor(contracts, editor)
    else:
        return _preview_in_terminal(contracts)


def _preview_in_editor(
    contracts: dict[str, str],
    editor: str,
) -> tuple[bool, dict[str, str]]:
    """Write to temp dir, open editor, read back."""
    with tempfile.TemporaryDirectory(prefix="specora-preview-") as tmpdir:
        tmp = Path(tmpdir)

        # Write all contracts
        for rel_path, content in contracts.items():
            file_path = tmp / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        # Show summary
        console.print()
        console.print(f"[bold]Generated {len(contracts)} contracts:[/bold]")
        for path in sorted(contracts.keys()):
            console.print(f"  [green]+[/green] {path}")
        console.print()
        console.print(f"[dim]Opening in {editor}... Review and close the editor to continue.[/dim]")

        # Open editor on the directory
        try:
            subprocess.run([editor, str(tmp)], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning("Editor failed: %s. Falling back to terminal preview.", e)
            return _preview_in_terminal(contracts)

        # Read back (possibly modified) content
        result = {}
        for rel_path in contracts.keys():
            file_path = tmp / rel_path
            if file_path.exists():
                result[rel_path] = file_path.read_text(encoding="utf-8")
            else:
                # User deleted the file — skip it
                logger.info("File removed during preview: %s", rel_path)

        # Ask for confirmation
        console.print()
        response = console.input("[bold]Write these contracts? [Y/n] [/bold]").strip().lower()
        accepted = response in ("", "y", "yes")

        return accepted, result


def _preview_in_terminal(contracts: dict[str, str]) -> tuple[bool, dict[str, str]]:
    """Display contracts in the terminal using Rich syntax highlighting."""
    console.print()
    console.print(f"[bold]Generated {len(contracts)} contracts:[/bold]")
    console.print()

    for path, content in sorted(contracts.items()):
        console.print(f"[bold cyan]{path}[/bold cyan]")
        syntax = Syntax(content, "yaml", theme="monokai", line_numbers=True)
        console.print(syntax)
        console.print()

    response = console.input("[bold]Write these contracts? [Y/n] [/bold]").strip().lower()
    accepted = response in ("", "y", "yes")

    return accepted, dict(contracts)

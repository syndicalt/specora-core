"""specora factory refine — modify existing contracts via natural language."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.syntax import Syntax

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from forge.diff.models import DiffOrigin
from forge.diff.store import DiffStore
from forge.diff.tracker import create_diff
from forge.normalize import normalize_contract
from forge.parser.validator import validate_contract

console = Console()

_SYSTEM_PROMPT = """You are a contract modification expert for the Specora CDD engine.
You receive an existing contract (YAML) and a natural-language instruction describing what to change.
Apply the requested change and return the complete modified contract.

Rules:
- metadata.name must be snake_case
- requires entries must be FQN format: kind/domain/name, all lowercase
- graph_edge must be SCREAMING_SNAKE_CASE
- Only change what the user asked for — preserve everything else
- Return the COMPLETE contract, not just the changed parts

Output format:
1. Brief explanation of what you changed (1-2 sentences)
2. The complete modified contract as a YAML code block (```yaml ... ```)
"""


@click.command("refine")
@click.argument("path", type=click.Path(exists=True))
@click.argument("instruction")
def factory_refine(path: str, instruction: str) -> None:
    """Modify an existing contract via natural language instruction."""
    contract_path = Path(path)

    if not contract_path.name.endswith(".contract.yaml"):
        console.print("[red]Not a contract file[/red]")
        sys.exit(1)

    # Load current contract
    try:
        original_content = contract_path.read_text(encoding="utf-8")
        before = yaml.safe_load(original_content)
    except Exception as e:
        console.print(f"[red]Failed to load:[/red] {e}")
        sys.exit(1)

    kind = before.get("kind", "?")
    metadata = before.get("metadata", {})
    fqn = f"{kind.lower()}/{metadata.get('domain', '?')}/{metadata.get('name', '?')}"

    console.print(f"[bold]Refining:[/bold] {fqn}")
    console.print(f"[dim]Instruction:[/dim] {instruction}\n")

    # Ask LLM to modify
    try:
        engine = LLMEngine.from_env()
    except EngineConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    prompt = (
        f"Contract FQN: {fqn}\n"
        f"Instruction: {instruction}\n\n"
        f"Current contract:\n```yaml\n{original_content}\n```"
    )

    try:
        response = engine.ask(question=prompt, system=_SYSTEM_PROMPT)
    except Exception as e:
        console.print(f"[red]LLM error:[/red] {e}")
        sys.exit(1)

    # Extract YAML from response
    after = _extract_yaml(response)
    if after is None:
        console.print("[red]Could not parse modified contract from LLM response[/red]")
        sys.exit(1)

    # Normalize
    normalize_contract(after)

    # Validate
    errors = validate_contract(after)
    real_errors = [e for e in errors if e.severity == "error"]
    if real_errors:
        console.print("[red]Modified contract has validation errors:[/red]")
        for e in real_errors:
            console.print(f"  {e.path}: {e.message}")
        sys.exit(1)

    # Show the result
    new_yaml = yaml.dump(
        after, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    console.print(Syntax(new_yaml, "yaml", theme="monokai", line_numbers=True))

    # Extract explanation
    explanation = _extract_explanation(response)
    if explanation:
        console.print(f"\n[dim]{explanation}[/dim]")

    # Confirm
    response_input = (
        console.input("\n[bold]Apply this change? [Y/n] [/bold]").strip().lower()
    )
    if response_input not in ("", "y", "yes"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # Write + record diff
    contract_path.write_text(new_yaml, encoding="utf-8")
    console.print(f"[green]wrote[/green] {contract_path}")

    diff = create_diff(
        contract_fqn=fqn,
        before=before,
        after=after,
        origin=DiffOrigin.FACTORY,
        origin_detail="factory:refine",
        reason=instruction,
    )
    store = DiffStore(root=Path(".forge/diffs"))
    store.save(diff)
    console.print(f"[dim]Diff recorded: {diff.id[:8]}[/dim]")


def _extract_yaml(response: str):
    """Extract YAML content from an LLM response containing a code block."""
    match = re.search(r"```ya?ml\s*\n(.*?)```", response, re.DOTALL)
    if match:
        try:
            return yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None
    # Fallback: try parsing the entire response as YAML
    try:
        return yaml.safe_load(response)
    except yaml.YAMLError:
        return None


def _extract_explanation(response: str) -> str:
    """Extract the explanation text before the first code block."""
    match = re.search(r"```", response)
    if match:
        return response[: match.start()].strip()[:200]
    return ""

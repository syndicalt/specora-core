"""specora factory refine — modify existing contracts via natural language."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from factory.paths import write_atomic
from forge.diff.models import DiffOrigin
from forge.diff.store import DiffStore
from forge.diff.tracker import create_diff
from forge.normalize import normalize_contract
from forge.parser.validator import validate_contract

console = Console()

_SYSTEM_PROMPT = """\
You are a contract modification expert for the Specora CDD engine.
You receive an existing contract (YAML) and a natural-language instruction
describing what to change.
Apply the requested change and return the complete modified contract.

Rules:
- metadata.name, metadata.domain and kind must not change — you are editing a
  contract in place, not creating a different one
- metadata.name must be snake_case
- requires entries must be FQN format: kind/domain/name, all lowercase
- graph_edge must be SCREAMING_SNAKE_CASE
- Money and other exact quantities use type `decimal` with
  constraints.precision/scale, never `number`
- Credentials, secrets and tokens must keep or gain `sensitive: true`
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
    target = Path(path)

    if not target.name.endswith(".contract.yaml"):
        console.print("[red]Not a contract file[/red]")
        sys.exit(1)

    # Load current contract
    try:
        original_content = target.read_text(encoding="utf-8")
        before = yaml.safe_load(original_content)
    except (OSError, yaml.YAMLError) as e:
        console.print(f"[red]Failed to load:[/red] {e}")
        sys.exit(1)

    if not isinstance(before, dict):
        console.print(f"[red]Not a contract:[/red] {target} does not parse to a mapping")
        sys.exit(1)

    kind = before.get("kind", "?")
    metadata = before.get("metadata") or {}
    fqn = f"{str(kind).lower()}/{metadata.get('domain', '?')}/{metadata.get('name', '?')}"

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

    # A refinement must not change what the contract *is*: the file's location
    # encodes its kind and name, so a renamed contract would leave a file whose
    # path and FQN disagree, and every `requires` pointing at the old FQN would
    # silently dangle.
    after_metadata = after.get("metadata") or {}
    after_fqn = (
        f"{str(after.get('kind', '?')).lower()}/"
        f"{after_metadata.get('domain', '?')}/{after_metadata.get('name', '?')}"
    )
    if after_fqn != fqn:
        console.print(
            f"[red]Refusing to apply:[/red] the model changed the contract's identity "
            f"from {fqn} to {after_fqn}. Rename the file deliberately instead."
        )
        sys.exit(1)

    # Validate
    errors = validate_contract(after)
    real_errors = [e for e in errors if e.severity == "error"]
    if real_errors:
        console.print("[red]Modified contract has validation errors:[/red]")
        for e in real_errors:
            console.print(f"  {e.path}: {escape(e.message)}")
        sys.exit(1)

    # Show the result
    new_yaml = yaml.dump(after, default_flow_style=False, sort_keys=False, allow_unicode=True)
    console.print(Syntax(new_yaml, "yaml", theme="monokai", line_numbers=True))

    # Extract explanation
    explanation = _extract_explanation(response)
    if explanation:
        console.print(f"\n[dim]{explanation}[/dim]")

    # Confirm
    response_input = console.input("\n[bold]Apply this change? [Y/n] [/bold]").strip().lower()
    if response_input not in ("", "y", "yes"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # Atomic: this overwrites the only copy of a source-of-truth contract, so
    # an interrupted write must not be able to truncate it.
    write_atomic(target, new_yaml)
    console.print(f"[green]wrote[/green] {target}")

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


def _extract_yaml(response: str) -> dict | None:
    """Extract a contract mapping from an LLM response containing a code block.

    Returns None rather than whatever YAML happened to parse: the response is
    mostly prose, and prose parses as a string, which the caller then handed to
    `normalize_contract` and crashed on.
    """
    candidates = re.findall(r"```ya?ml\s*\n(.*?)```", response, re.DOTALL) or [response]
    for candidate in candidates:
        try:
            parsed = yaml.safe_load(candidate)
        except yaml.YAMLError as e:
            console.print(f"[dim]Skipping unparseable block: {e}[/dim]")
            continue
        if isinstance(parsed, dict) and "apiVersion" in parsed:
            return parsed
    return None


def _extract_explanation(response: str) -> str:
    """Extract the explanation text before the first code block."""
    match = re.search(r"```", response)
    if match:
        return response[: match.start()].strip()[:200]
    return ""

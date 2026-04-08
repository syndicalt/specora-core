"""specora factory explain — LLM explains a contract in plain English."""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.markdown import Markdown

from engine.config import EngineConfigError
from engine.context import build_system_prompt
from engine.engine import LLMEngine

console = Console()


@click.command("explain")
@click.argument("path", type=click.Path(exists=True))
def factory_explain(path: str) -> None:
    """Explain a contract in plain English using the LLM."""
    contract_path = Path(path)

    if not contract_path.name.endswith(".contract.yaml"):
        console.print("[red]Not a contract file[/red] (expected .contract.yaml)")
        sys.exit(1)

    # Load the contract
    try:
        content = contract_path.read_text(encoding="utf-8")
        contract = yaml.safe_load(content)
    except Exception as e:
        console.print(f"[red]Failed to load:[/red] {e}")
        sys.exit(1)

    kind = contract.get("kind", "?")
    metadata = contract.get("metadata", {})
    fqn = f"{kind.lower()}/{metadata.get('domain', '?')}/{metadata.get('name', '?')}"

    console.print(f"[bold]Explaining:[/bold] {fqn}\n")

    # Ask the LLM
    try:
        engine = LLMEngine.from_env()
    except EngineConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    system = build_system_prompt("explain")
    question = f"Explain this {kind} contract:\n\n```yaml\n{content}\n```"

    try:
        explanation = engine.ask(question=question, system=system)
    except Exception as e:
        console.print(f"[red]LLM error:[/red] {e}")
        sys.exit(1)

    console.print(Markdown(explanation))

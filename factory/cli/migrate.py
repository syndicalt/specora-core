"""specora factory migrate — import from external schema formats."""
from __future__ import annotations

import sys
import re
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.syntax import Syntax

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from forge.normalize import normalize_contract
from forge.parser.validator import validate_contract

console = Console()

_SYSTEM_PROMPT = """You are a schema migration expert for the Specora CDD engine.
You receive a schema file (OpenAPI, SQL DDL, or Prisma) and convert it into Specora Entity contracts.

For each entity/table/model found, output a Specora Entity contract in YAML.

Contract format:
```yaml
apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: snake_case_name
  domain: {domain}
  description: "Brief description"
requires:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable
spec:
  fields:
    field_name:
      type: string|integer|number|boolean|text|datetime|date|uuid|email|array|object
      required: true|false
      description: "Field description"
      references:  # only if it's a foreign key
        entity: entity/{domain}/target_name
        display: name
        graph_edge: RELATIONSHIP_NAME
  mixins:
    - mixin/stdlib/timestamped
    - mixin/stdlib/identifiable
```

Rules:
- Names must be snake_case
- FQNs must be kind/domain/name format, all lowercase
- graph_edge must be SCREAMING_SNAKE_CASE
- Map SQL types: VARCHAR/TEXT->string, INT/BIGINT->integer, DECIMAL/FLOAT->number, BOOLEAN->boolean, TIMESTAMP->datetime, DATE->date, UUID->uuid
- Map OpenAPI types: string->string, integer->integer, number->number, boolean->boolean, array->array, object->object
- Include mixin/stdlib/timestamped and mixin/stdlib/identifiable by default
- Detect foreign keys and create references with graph edges

Output each contract separated by `---` (YAML document separator).
"""


@click.command("migrate")
@click.argument("source", type=click.Path(exists=True))
@click.option("--domain", "-d", required=True, help="Target domain name")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["auto", "openapi", "sql", "prisma"]),
    default="auto",
    help="Source format",
)
def factory_migrate(source: str, domain: str, fmt: str) -> None:
    """Import external schemas into Specora contracts via LLM."""
    source_path = Path(source)
    content = source_path.read_text(encoding="utf-8")

    # Auto-detect format
    if fmt == "auto":
        fmt = _detect_format(source_path, content)
        console.print(f"[dim]Detected format: {fmt}[/dim]")

    console.print(f"[bold]Migrating {source_path.name} → domain '{domain}'[/bold]")

    # Initialize LLM
    try:
        engine = LLMEngine.from_env()
    except EngineConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    system = _SYSTEM_PROMPT.replace("{domain}", domain)
    prompt = f"Source format: {fmt}\nDomain: {domain}\n\nSource file:\n```\n{content}\n```"

    try:
        response = engine.ask(question=prompt, system=system)
    except Exception as e:
        console.print(f"[red]LLM error:[/red] {e}")
        sys.exit(1)

    # Parse contracts from response
    contracts = _extract_contracts(response)
    if not contracts:
        console.print("[red]No contracts could be extracted from the LLM response.[/red]")
        sys.exit(1)

    # Normalize and validate each
    valid_contracts: dict[str, str] = {}
    for contract in contracts:
        normalize_contract(contract)
        errors = validate_contract(contract)
        real_errors = [e for e in errors if e.severity == "error"]

        kind = contract.get("kind", "Entity").lower()
        name = contract.get("metadata", {}).get("name", "unknown")
        kind_dirs = {
            "entity": "entities",
            "workflow": "workflows",
            "route": "routes",
            "page": "pages",
        }
        subdir = kind_dirs.get(kind, "entities")
        rel_path = f"{subdir}/{name}.contract.yaml"

        if real_errors:
            console.print(f"[yellow]Skipping {rel_path} ({len(real_errors)} errors)[/yellow]")
            for e in real_errors[:3]:
                console.print(f"  {e.message}")
            continue

        yaml_str = yaml.dump(
            contract,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        valid_contracts[rel_path] = yaml_str

    if not valid_contracts:
        console.print("[red]No valid contracts produced.[/red]")
        sys.exit(1)

    # Preview
    console.print(f"\n[bold]Generated {len(valid_contracts)} contracts:[/bold]")
    for path, yaml_content in sorted(valid_contracts.items()):
        console.print(f"\n[cyan]{path}[/cyan]")
        console.print(Syntax(yaml_content, "yaml", theme="monokai", line_numbers=True))

    response_input = (
        console.input(
            f"\n[bold]Write {len(valid_contracts)} contracts to domains/{domain}/? [Y/n] [/bold]"
        )
        .strip()
        .lower()
    )
    if response_input not in ("", "y", "yes"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # Write
    domain_path = Path("domains") / domain
    for rel_path, yaml_content in valid_contracts.items():
        file_path = domain_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(yaml_content, encoding="utf-8")
        console.print(f"  [green]wrote[/green] {file_path}")

    console.print(
        f"\n[bold green]Migrated {len(valid_contracts)} contracts to domains/{domain}/[/bold green]"
    )


def _detect_format(path: Path, content: str) -> str:
    """Auto-detect source file format from extension and content."""
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        if "openapi" in content[:200].lower() or "paths:" in content[:500]:
            return "openapi"
        return "openapi"
    if suffix == ".sql":
        return "sql"
    if suffix == ".prisma":
        return "prisma"
    if "CREATE TABLE" in content.upper():
        return "sql"
    if "model " in content and "@@" in content:
        return "prisma"
    return "openapi"


def _extract_contracts(response: str) -> list[dict]:
    """Extract YAML contracts from LLM response."""
    contracts: list[dict] = []

    # Try to find YAML code blocks
    blocks = re.findall(r"```ya?ml\s*\n(.*?)```", response, re.DOTALL)
    if blocks:
        for block in blocks:
            # Split on YAML document separator
            docs = block.split("\n---\n")
            for doc in docs:
                doc = doc.strip()
                if not doc:
                    continue
                try:
                    parsed = yaml.safe_load(doc)
                    if isinstance(parsed, dict) and "apiVersion" in parsed:
                        contracts.append(parsed)
                except yaml.YAMLError:
                    continue
        return contracts

    # Try the whole response as multi-doc YAML
    try:
        for doc in yaml.safe_load_all(response):
            if isinstance(doc, dict) and "apiVersion" in doc:
                contracts.append(doc)
    except yaml.YAMLError:
        pass

    return contracts

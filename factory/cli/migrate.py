"""specora factory migrate — import from external schema formats."""

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
from factory.paths import UnsafeNameError, contract_path, safe_name, write_atomic
from forge.normalize import normalize_contract
from forge.parser.validator import validate_contract

console = Console()

_SYSTEM_PROMPT = """\
You are a schema migration expert for the Specora CDD engine.
You receive a schema file (OpenAPI, SQL DDL, or Prisma) and convert it into
Specora Entity contracts.

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
      type: string|integer|number|decimal|boolean|text|datetime|date|uuid|email|array|object
      required: true|false
      description: "Field description"
      sensitive: true  # only for credentials, secrets and tokens
      constraints:     # only where the source declares them
        precision: 18  # decimal only
        scale: 2       # decimal only
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
- metadata.domain must be exactly `{domain}` on every contract
- FQNs must be kind/domain/name format, all lowercase
- graph_edge must be SCREAMING_SNAKE_CASE
- Emit ONLY the keys shown above. Any other key is rejected by the meta-schema.
- Map SQL types: VARCHAR/TEXT->string, INT/BIGINT->integer,
  NUMERIC/DECIMAL/MONEY->decimal (carry precision and scale across),
  FLOAT/REAL/DOUBLE->number, BOOLEAN->boolean, TIMESTAMP->datetime,
  DATE->date, UUID->uuid
- Map OpenAPI types: string->string, integer->integer, boolean->boolean,
  array->array, object->object; number with format "decimal"/"currency"
  ->decimal, otherwise number
- Never map money to `number`; `number` is inexact floating point
- Mark password, password_hash, secret, token and api_key columns
  `sensitive: true` — without it the generated API returns them
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
@click.option(
    "--input",
    "-i",
    "input_dir",
    default="domains/",
    type=click.Path(),
    help="Base directory for contract output (default: domains/)",
)
def factory_migrate(source: str, domain: str, fmt: str, input_dir: str) -> None:
    """Import external schemas into Specora contracts via LLM."""
    contracts_base = Path(input_dir)
    try:
        domain = safe_name(domain, what="domain name")
    except UnsafeNameError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    source_path = Path(source)
    try:
        content = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        console.print(f"[red]Failed to read {source_path}:[/red] {e}")
        sys.exit(1)

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
    valid_contracts: dict[Path, str] = {}
    for index, contract in enumerate(contracts):
        # The domain is the operator's choice, not the model's. Leaving the
        # model's value in place put contracts under `domains/<--domain>/`
        # while declaring a different domain, which then fails to resolve.
        metadata = contract.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["domain"] = domain

        normalize_contract(contract)

        kind = str(contract.get("kind", "Entity")).lower()
        name = (contract.get("metadata") or {}).get("name", "")
        label = f"contract #{index + 1} ({kind}/{name or '?'})"

        try:
            # `kind` and `name` are model output on their way to a file path.
            target = contract_path(contracts_base, domain, kind, name)
        except UnsafeNameError as e:
            console.print(f"[yellow]Skipping {label}:[/yellow] {e}")
            continue

        real_errors = [e for e in validate_contract(contract) if e.severity == "error"]
        if real_errors:
            console.print(f"[yellow]Skipping {label} ({len(real_errors)} errors)[/yellow]")
            for e in real_errors[:3]:
                console.print(f"  {e.path}: {escape(e.message)}")
            continue

        if target in valid_contracts:
            console.print(f"[yellow]Skipping {label}: duplicate of an earlier contract[/yellow]")
            continue

        valid_contracts[target] = yaml.dump(
            contract,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    skipped = len(contracts) - len(valid_contracts)
    if skipped:
        console.print(
            f"[yellow]{skipped} of {len(contracts)} extracted contracts skipped.[/yellow]"
        )

    if not valid_contracts:
        console.print("[red]No valid contracts produced.[/red]")
        sys.exit(1)

    existing = [p for p in valid_contracts if p.exists()]
    if existing:
        console.print("[red]Refusing to overwrite existing contracts:[/red]")
        for p in existing:
            console.print(f"  {p}")
        sys.exit(1)

    # Preview
    console.print(f"\n[bold]Generated {len(valid_contracts)} contracts:[/bold]")
    for path, yaml_content in sorted(valid_contracts.items()):
        console.print(f"\n[cyan]{path}[/cyan]")
        console.print(Syntax(yaml_content, "yaml", theme="monokai", line_numbers=True))

    domain_path = contracts_base / domain
    response_input = (
        console.input(
            f"\n[bold]Write {len(valid_contracts)} contracts to {domain_path}/? [Y/n] [/bold]"
        )
        .strip()
        .lower()
    )
    if response_input not in ("", "y", "yes"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    for file_path, yaml_content in sorted(valid_contracts.items()):
        write_atomic(file_path, yaml_content)
        console.print(f"  [green]wrote[/green] {file_path}")

    console.print(
        f"\n[bold green]Migrated {len(valid_contracts)} contracts to {domain_path}/[/bold green]"
    )
    console.print(
        "[dim]Entities have no Route or Page contracts yet — add them with "
        "'specora factory add route' and 'specora factory add page'.[/dim]"
    )


def _detect_format(path: Path, content: str) -> str:
    """Auto-detect source file format from extension and content."""
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml", ".json"):
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
    """Extract YAML contracts from an LLM response.

    Every document the model produced but this function could not use is
    reported. Dropping them silently made a partial migration — three of five
    tables imported — indistinguishable from a complete one.
    """
    contracts: list[dict] = []
    rejected = 0

    for doc in _candidate_documents(response):
        try:
            parsed = yaml.safe_load(doc)
        except yaml.YAMLError as e:
            rejected += 1
            console.print(f"[yellow]Discarding an unparseable YAML document:[/yellow] {e}")
            continue
        if isinstance(parsed, dict) and "apiVersion" in parsed:
            contracts.append(parsed)
        elif parsed is not None:
            rejected += 1
            console.print("[yellow]Discarding a document with no 'apiVersion' key.[/yellow]")

    if rejected:
        console.print(f"[yellow]{rejected} document(s) from the model were unusable.[/yellow]")

    return contracts


def _candidate_documents(response: str) -> list[str]:
    """Split an LLM response into candidate YAML documents.

    Fenced blocks are preferred, but the whole response is still tried when
    none of them yield anything — a model that answers without a fence used to
    produce "No contracts could be extracted" with the payload right there.
    """
    docs: list[str] = []
    for block in re.findall(r"```ya?ml\s*\n(.*?)```", response, re.DOTALL):
        docs.extend(d.strip() for d in block.split("\n---\n") if d.strip())
    if docs:
        return docs
    return [d.strip() for d in response.split("\n---\n") if d.strip()]

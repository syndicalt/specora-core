"""specora factory chat — agentic domain conversation with tool use.

The chat command is more than a conversation — it's a domain modeling
agent. The LLM can propose contract changes and, with user approval,
execute them directly. Every action is confirmed before execution.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Optional

import click
import yaml
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.rule import Rule

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from engine.providers.base import Message, ToolDefinition

console = Console()

# Module-level contracts base directory — set by the CLI command
_contracts_base = Path("domains")


# ─── Domain Context ──────────────────────────────────────────────────

def _discover_domains() -> list[str]:
    if not _contracts_base.exists():
        return []
    return [d.name for d in _contracts_base.iterdir() if d.is_dir() and not d.name.startswith(".")]


def _build_domain_context(domain: str) -> str:
    from forge.parser.loader import load_all_contracts
    domain_path = _contracts_base / domain
    if not domain_path.exists():
        return f"Domain '{domain}' has no contracts yet."
    try:
        contracts = load_all_contracts(domain_path)
    except Exception:
        return f"Domain '{domain}' exists but contracts could not be loaded."
    if not contracts:
        return f"Domain '{domain}' has no contracts."

    lines = [f"Domain '{domain}' has {len(contracts)} contracts:\n"]
    for fqn, contract in sorted(contracts.items()):
        kind = contract.get("kind", "?")
        desc = contract.get("metadata", {}).get("description", "")
        fields = list(contract.get("spec", {}).get("fields", {}).keys())
        field_str = f"  fields: {', '.join(fields[:8])}" if fields else ""
        lines.append(f"  {fqn} ({kind})")
        if desc:
            lines.append(f"    {desc}")
        if field_str:
            lines.append(f"    {field_str}")
    return "\n".join(lines)


def _load_contract_yaml(domain: str, kind: str, name: str) -> Optional[str]:
    kind_dirs = {"entity": "entities", "workflow": "workflows", "route": "routes", "page": "pages"}
    subdir = kind_dirs.get(kind, f"{kind}s")
    path = _contracts_base / domain / subdir / f"{name}.contract.yaml"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


# ─── Tool Definitions ────────────────────────────────────────────────

TOOLS = [
    ToolDefinition(
        name="propose_entity",
        description="Propose creating a new Entity contract. The user will be asked to confirm before it's written to disk.",
        parameters={
            "type": "object",
            "required": ["name", "description", "fields"],
            "properties": {
                "name": {"type": "string", "description": "Entity name in snake_case (e.g., 'review', 'appointment')"},
                "description": {"type": "string", "description": "One-sentence description of the entity"},
                "fields": {
                    "type": "object",
                    "description": "Map of field_name to field definition. Each field has 'type' (string/integer/text/boolean/date/datetime/uuid/email/number/array) and optionally 'required', 'description', 'enum', 'references'.",
                    "additionalProperties": {"type": "object"},
                },
                "mixins": {
                    "type": "array", "items": {"type": "string"},
                    "description": "List of mixin FQNs (e.g., ['mixin/stdlib/timestamped', 'mixin/stdlib/identifiable'])",
                },
                "state_machine": {"type": "string", "description": "Workflow FQN if entity needs a state machine"},
            },
        },
    ),
    ToolDefinition(
        name="propose_modification",
        description="Propose modifying an existing contract. The user will be asked to confirm before changes are applied.",
        parameters={
            "type": "object",
            "required": ["contract_fqn", "instruction"],
            "properties": {
                "contract_fqn": {"type": "string", "description": "FQN of contract to modify (e.g., 'entity/library/book')"},
                "instruction": {"type": "string", "description": "Natural language description of the change"},
            },
        },
    ),
    ToolDefinition(
        name="validate_domain",
        description="Run validation on the current domain's contracts.",
        parameters={"type": "object", "properties": {}},
    ),
]

SYSTEM_TEMPLATE = """You are a domain modeling expert for the Specora Contract-Driven Development engine.
You are an agent that can both discuss and build. When the developer describes something they
want to add or change, use your tools to propose it. ALWAYS use tools to propose changes —
never just describe what they should do manually.

You have tools to:
- propose_entity: Create a new entity contract
- propose_modification: Modify an existing contract
- validate_domain: Check all contracts are valid

IMPORTANT: Every proposal will be shown to the developer for confirmation before executing.
You don't need to ask "shall I create this?" — just use the tool. The system handles confirmation.

{domain_context}

Rules for contract content:
- Entity names: snake_case (e.g., review, todo_item)
- Field types: string, integer, number, boolean, text, array, object, datetime, date, uuid, email
- References to other entities use: {{"references": {{"entity": "entity/DOMAIN/NAME", "display": "name", "graph_edge": "RELATIONSHIP_NAME"}}}}
- graph_edge must be SCREAMING_SNAKE_CASE (e.g., REVIEWED_BY, ASSIGNED_TO)
- Always include mixin/stdlib/timestamped and mixin/stdlib/identifiable

Be concise. Propose concrete changes. Let the tools do the work.
"""


# ─── Tool Execution ──────────────────────────────────────────────────

def _execute_tool(tool_name: str, tool_input: dict, domain: str) -> str:
    """Execute a tool call, always asking the user first. Returns result message."""

    if tool_name == "propose_entity":
        return _propose_entity(tool_input, domain)
    elif tool_name == "propose_modification":
        return _propose_modification(tool_input, domain)
    elif tool_name == "validate_domain":
        return _validate_domain(domain)
    else:
        return f"Unknown tool: {tool_name}"


def _propose_entity(params: dict, domain: str) -> str:
    from factory.emitters.entity_emitter import emit_entity
    from forge.parser.validator import validate_contract

    name = params["name"]
    data = {
        "description": params.get("description", f"A {name} entity"),
        "fields": params.get("fields", {}),
        "mixins": params.get("mixins", ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"]),
    }
    if params.get("state_machine"):
        data["state_machine"] = params["state_machine"]

    yaml_str = emit_entity(name, domain, data)
    contract = yaml.safe_load(yaml_str)

    # Show proposal
    console.print()
    console.print(Rule(f"[bold cyan]Proposed: entity/{domain}/{name}[/bold cyan]", style="cyan"))
    console.print(Syntax(yaml_str, "yaml", theme="monokai", line_numbers=True, padding=1))

    # Validate
    errors = validate_contract(contract)
    real_errors = [e for e in errors if e.severity == "error"]
    if real_errors:
        console.print(f"  [yellow]⚠ {len(real_errors)} validation warning(s) — will be auto-healed[/yellow]")

    # Ask
    console.print()
    try:
        response = console.input("  [bold]Write this contract? [Y/n] [/bold]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "User cancelled."

    if response not in ("", "y", "yes"):
        return "User declined. Contract was NOT created."

    # Write
    from forge.normalize import normalize_name
    safe_name = normalize_name(name)
    path = _contracts_base / domain / "entities" / f"{safe_name}.contract.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_str, encoding="utf-8")
    console.print(f"  [green]✓ Wrote {path}[/green]")

    # Also generate route + page
    from factory.emitters.route_emitter import emit_route
    from factory.emitters.page_emitter import emit_page

    entity_fqn = f"entity/{domain}/{safe_name}"
    plural = safe_name + "s"
    field_names = list(params.get("fields", {}).keys())

    route_path = _contracts_base / domain / "routes" / f"{plural}.contract.yaml"
    route_path.parent.mkdir(parents=True, exist_ok=True)
    route_path.write_text(emit_route(plural, domain, entity_fqn), encoding="utf-8")
    console.print(f"  [green]✓ Wrote {route_path}[/green]")

    page_path = _contracts_base / domain / "pages" / f"{plural}.contract.yaml"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(emit_page(plural, domain, entity_fqn, field_names), encoding="utf-8")
    console.print(f"  [green]✓ Wrote {page_path}[/green]")

    console.print()
    return f"Created entity/{domain}/{safe_name} with route and page contracts."


def _propose_modification(params: dict, domain: str) -> str:
    fqn = params["contract_fqn"]
    instruction = params["instruction"]

    # Find the contract
    parts = fqn.split("/")
    if len(parts) != 3:
        return f"Invalid FQN: {fqn}"

    kind, dom, name = parts
    kind_dirs = {"entity": "entities", "workflow": "workflows", "route": "routes", "page": "pages"}
    subdir = kind_dirs.get(kind, f"{kind}s")
    path = _contracts_base / dom / subdir / f"{name}.contract.yaml"

    if not path.exists():
        return f"Contract not found: {path}"

    console.print()
    console.print(Rule(f"[bold yellow]Modify: {fqn}[/bold yellow]", style="yellow"))
    console.print(f"  [dim]Instruction: {instruction}[/dim]")
    console.print()

    try:
        response = console.input("  [bold]Apply this modification? [Y/n] [/bold]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "User cancelled."

    if response not in ("", "y", "yes"):
        return "User declined. No changes made."

    # Execute the refine via LLM
    from factory.cli.refine import factory_refine
    try:
        ctx = factory_refine.make_context("refine", [str(path), instruction])
        factory_refine.invoke(ctx)
        return f"Modified {fqn}."
    except SystemExit:
        return f"Modification completed."
    except Exception as e:
        return f"Error: {e}"


def _validate_domain(domain: str) -> str:
    from forge.parser.loader import load_all_contracts
    from forge.parser.validator import validate_all

    domain_path = _contracts_base / domain
    contracts = load_all_contracts(domain_path)
    errors = validate_all(contracts)
    real_errors = [e for e in errors if e.severity == "error"]

    if not real_errors:
        console.print(f"  [green]✓ All {len(contracts)} contracts are valid[/green]")
        return f"All {len(contracts)} contracts are valid."
    else:
        console.print(f"  [red]✗ {len(real_errors)} error(s)[/red]")
        for e in real_errors[:5]:
            console.print(f"    {e.contract_fqn}: {e.message}")
        return f"{len(real_errors)} validation error(s) found."


# ─── Main Chat Loop ─────────────────────────────────────────────────

@click.command("chat")
@click.option("--domain", "-d", default="", help="Domain to chat about")
@click.option("--input", "-i", "input_dir", default="domains/", type=click.Path(),
              help="Base directory for contracts (default: domains/)")
def factory_chat(domain: str, input_dir: str) -> None:
    """Agentic domain conversation — discuss, propose, and build contracts."""
    global _contracts_base
    _contracts_base = Path(input_dir)

    if not domain:
        domains = _discover_domains()
        if len(domains) == 1:
            domain = domains[0]
        elif len(domains) == 0:
            console.print("[red]No domains found.[/red] Run 'specora init <name>' first.")
            sys.exit(1)
        else:
            console.print(f"[yellow]Multiple domains:[/yellow] {', '.join(domains)}")
            console.print("Use --domain to specify which one.")
            sys.exit(1)

    try:
        engine = LLMEngine.from_env()
    except EngineConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    domain_context = _build_domain_context(domain)
    system_prompt = SYSTEM_TEMPLATE.format(domain_context=domain_context)

    console.print()
    console.print(Rule(f"[bold magenta]Chat: {domain}[/bold magenta]", style="magenta"))
    console.print(f"  [dim]Model: {engine.model_id}  •  Type 'exit' or Ctrl+D to quit[/dim]")
    console.print()

    messages: list[Message] = []

    while True:
        try:
            user_input = console.input("[bold magenta]❯ [/bold magenta]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            break

        messages.append(Message(role="user", content=user_input))

        try:
            with console.status("[magenta]Thinking…[/magenta]", spinner="dots"):
                response = engine.chat(
                    messages,
                    system=system_prompt,
                    tools=TOOLS,
                    temperature=0.0,
                )

            # Handle tool calls
            while response.tool_calls:
                # Process each tool call
                tool_results = []
                for tc in response.tool_calls:
                    tool_name = tc.get("name", "")
                    tool_input = tc.get("input", {})
                    tool_id = tc.get("id", "")

                    result_text = _execute_tool(tool_name, tool_input, domain)
                    tool_results.append({"tool_use_id": tool_id, "content": result_text})

                # Add assistant message with tool calls + tool results
                messages.append(Message(role="assistant", content=response.content or "", tool_calls=response.tool_calls))
                messages.append(Message(role="tool", content="", tool_results=tool_results))

                # Refresh domain context after tool execution
                domain_context = _build_domain_context(domain)
                system_prompt = SYSTEM_TEMPLATE.format(domain_context=domain_context)

                # Continue the conversation
                with console.status("[magenta]Thinking…[/magenta]", spinner="dots"):
                    response = engine.chat(
                        messages,
                        system=system_prompt,
                        tools=TOOLS,
                        temperature=0.0,
                    )

            # Display final text response
            if response.content:
                messages.append(Message(role="assistant", content=response.content))
                console.print()
                console.print(Markdown(response.content))
                console.print()

        except Exception as e:
            console.print(f"  [red]Error:[/red] {e}")
            if messages and messages[-1].role == "user":
                messages.pop()

    console.print(Rule(style="dim"))
    console.print("  [dim]Chat ended.[/dim]")
    console.print()

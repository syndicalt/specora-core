"""specora factory chat — agentic domain conversation with tool use.

The chat command is more than a conversation — it's a domain modeling
agent. The LLM can propose contract changes and, with user approval,
execute them directly. Every action is confirmed before execution.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.rule import Rule
from rich.syntax import Syntax

from engine.config import EngineConfigError
from engine.engine import LLMEngine
from engine.providers.base import Message, ToolDefinition
from factory.paths import UnsafeNameError, contract_path, safe_name, write_atomic
from forge.targets.naming import pluralize

logger = logging.getLogger(__name__)
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
    except Exception as exc:
        # The model plans against this context; "could not be loaded" with the
        # cause discarded left both the model and the developer guessing at a
        # broken YAML file they were never shown.
        logger.warning("Could not load contracts for domain '%s'", domain, exc_info=True)
        console.print(f"  [red]Could not load contracts for '{domain}':[/red] {exc}")
        return f"Domain '{domain}' exists but its contracts could not be loaded: {exc}"
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


# ─── Tool Definitions ────────────────────────────────────────────────

TOOLS = [
    ToolDefinition(
        name="propose_entity",
        description=(
            "Propose creating a new Entity contract. The user will be asked to "
            "confirm before it's written to disk."
        ),
        parameters={
            "type": "object",
            "required": ["name", "description", "fields"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Entity name in snake_case (e.g., 'review', 'appointment')",
                },
                "description": {
                    "type": "string",
                    "description": "One-sentence description of the entity",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Map of field_name to field definition. Each field has 'type' "
                        "(string/integer/number/decimal/text/boolean/date/datetime/uuid/"
                        "email/array/object) and optionally 'required', 'description', "
                        "'enum', 'references', 'constraints', and 'sensitive' (true for "
                        "credentials, which are then never returned by the API). Use "
                        "'decimal' for money, never 'number'."
                    ),
                    "additionalProperties": {"type": "object"},
                },
                "mixins": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of mixin FQNs "
                        "(e.g., ['mixin/stdlib/timestamped', 'mixin/stdlib/identifiable'])"
                    ),
                },
                "state_machine": {
                    "type": "string",
                    "description": "Workflow FQN if entity needs a state machine",
                },
            },
        },
    ),
    ToolDefinition(
        name="propose_modification",
        description=(
            "Propose modifying an existing contract. The user will be asked to "
            "confirm before changes are applied."
        ),
        parameters={
            "type": "object",
            "required": ["contract_fqn", "instruction"],
            "properties": {
                "contract_fqn": {
                    "type": "string",
                    "description": "FQN of contract to modify (e.g., 'entity/library/book')",
                },
                "instruction": {
                    "type": "string",
                    "description": "Natural language description of the change",
                },
            },
        },
    ),
    ToolDefinition(
        name="validate_domain",
        description="Run validation on the current domain's contracts.",
        parameters={"type": "object", "properties": {}},
    ),
]

SYSTEM_TEMPLATE = """\
You are a domain modeling expert for the Specora Contract-Driven Development engine.
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
- Field types: string, integer, number, decimal, boolean, text, array, object,
  datetime, date, uuid, email
- Use `decimal` for money and every other exact quantity, with
  constraints.precision and constraints.scale (defaults 18 and 2). `number` is
  inexact floating point — never use it for money.
- Mark any credential, secret, token, or password field `sensitive: true`.
  Response models are built from the entity's full field list, so a field
  without that flag is published by the API.
- References to other entities use:
  {{"references": {{"entity": "entity/DOMAIN/NAME", "display": "name",
  "graph_edge": "RELATIONSHIP_NAME"}}}}
- graph_edge must be SCREAMING_SNAKE_CASE (e.g., REVIEWED_BY, ASSIGNED_TO)
- Always include mixin/stdlib/timestamped and mixin/stdlib/identifiable

Be concise. Propose concrete changes. Let the tools do the work.
"""


# ─── Tool Execution ──────────────────────────────────────────────────


def _execute_tool(tool_name: str, tool_input: dict, domain: str) -> str:
    """Execute a tool call, always asking the user first. Returns result message.

    Every path returns a string. The conversation requires one tool result per
    tool call, so an exception escaping here would desynchronise the message
    history for the remainder of the session, not just fail this turn.
    """
    handlers = {
        "propose_entity": lambda: _propose_entity(tool_input, domain),
        "propose_modification": lambda: _propose_modification(tool_input, domain),
        "validate_domain": lambda: _validate_domain(domain),
    }
    handler = handlers.get(tool_name)
    if handler is None:
        return f"Unknown tool: {tool_name}"

    if not isinstance(tool_input, dict):
        return f"Rejected: {tool_name} arguments must be an object, got {type(tool_input).__name__}"

    try:
        return handler()
    except KeyError as exc:
        return f"Rejected: {tool_name} is missing required argument {exc}."
    except Exception as exc:
        logger.exception("Tool %s failed", tool_name)
        console.print(f"  [red]{tool_name} failed:[/red] {exc}")
        return f"{tool_name} failed: {exc}"


def _propose_entity(params: dict, domain: str) -> str:
    from factory.emitters.base import EmitterError
    from factory.emitters.entity_emitter import emit_entity
    from factory.emitters.page_emitter import emit_page, page_columns
    from factory.emitters.route_emitter import emit_route

    fields = params.get("fields") or {}
    state_machine = params.get("state_machine") or ""

    # `name` and `state_machine` arrive from a tool call the model composed
    # from the developer's prose, so both are untrusted: `name` reaches a file
    # path and `state_machine` reaches a `requires` entry.
    try:
        name = safe_name(params.get("name"), what="entity name")
        entity_path = contract_path(_contracts_base, domain, "entity", name)
        plural = pluralize(name)
        route_path = contract_path(_contracts_base, domain, "route", plural)
        page_path = contract_path(_contracts_base, domain, "page", plural)
    except UnsafeNameError as exc:
        console.print(f"  [red]Rejected proposal:[/red] {escape(str(exc))}")
        return f"Rejected: {exc}. Propose a snake_case name."

    data = {
        "description": params.get("description", f"A {name} entity"),
        "fields": fields,
        "mixins": params.get("mixins", ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"]),
    }
    if state_machine:
        data["state_machine"] = state_machine

    entity_fqn = f"entity/{domain}/{name}"

    # Emit everything before writing anything. The emitters validate against
    # the meta-schemas and raise, so a contract that would not compile never
    # reaches disk — this path previously printed schema errors as "validation
    # warnings ... will be auto-healed" and wrote the file regardless.
    try:
        yaml_str = emit_entity(name, domain, data)
        route_yaml = emit_route(plural, domain, entity_fqn, state_machine)
        page_yaml = emit_page(plural, domain, entity_fqn, page_columns(fields))
    except EmitterError as exc:
        console.print(f"  [red]Rejected proposal:[/red] {escape(str(exc))}")
        return f"Rejected, nothing was written: {exc}"

    existing = [p for p in (entity_path, route_path, page_path) if p.exists()]
    if existing:
        console.print(f"  [red]Refusing to overwrite:[/red] {', '.join(str(p) for p in existing)}")
        return (
            f"Rejected: {name} already exists. Use propose_modification to change "
            "an existing contract."
        )

    console.print()
    console.print(Rule(f"[bold cyan]Proposed: {entity_fqn}[/bold cyan]", style="cyan"))
    console.print(Syntax(yaml_str, "yaml", theme="monokai", line_numbers=True, padding=1))
    if state_machine:
        console.print(
            f"  [yellow]Note:[/yellow] this entity binds {state_machine}, which must exist "
            "before the domain will compile."
        )

    console.print()
    try:
        response = console.input("  [bold]Write this contract? [Y/n] [/bold]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "User cancelled."

    if response not in ("", "y", "yes"):
        return "User declined. Contract was NOT created."

    for path, content in (
        (entity_path, yaml_str),
        (route_path, route_yaml),
        (page_path, page_yaml),
    ):
        write_atomic(path, content)
        console.print(f"  [green]✓ Wrote {path}[/green]")

    console.print()
    return f"Created {entity_fqn} with route and page contracts."


def _propose_modification(params: dict, domain: str) -> str:
    fqn = params["contract_fqn"]
    instruction = params["instruction"]

    parts = fqn.split("/")
    if len(parts) != 3:
        return f"Invalid FQN: {fqn}"

    kind, dom, name = parts
    if dom != domain:
        # The chat session is scoped to one domain; letting the model reach
        # into another one silently edits contracts the developer is not
        # looking at.
        return f"Rejected: {fqn} is not in domain '{domain}', which this session is scoped to."

    # `dom` and `name` come straight from the model. Before this check,
    # "entity/../secrets" resolved to a path outside the contracts tree and
    # the refine step below would have rewritten whatever was there.
    try:
        path = contract_path(_contracts_base, dom, kind, name)
    except UnsafeNameError as exc:
        return f"Rejected: {exc}"

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
        return "Modification completed."
    except Exception as e:
        return f"Error: {e}"


def _validate_domain(domain: str) -> str:
    from forge.parser.loader import load_all_contracts
    from forge.parser.validator import validate_all

    domain_path = _contracts_base / domain
    try:
        contracts = load_all_contracts(domain_path)
    except Exception as exc:
        # A tool call must always return a result; letting this escape would
        # break the assistant/tool message pairing for the rest of the session.
        logger.warning("validate_domain could not load '%s'", domain, exc_info=True)
        console.print(f"  [red]✗ Could not load contracts:[/red] {exc}")
        return f"Could not load contracts for '{domain}': {exc}"

    errors = validate_all(contracts)
    real_errors = [e for e in errors if e.severity == "error"]

    if not real_errors:
        console.print(f"  [green]✓ All {len(contracts)} contracts are valid[/green]")
        return f"All {len(contracts)} contracts are valid."
    else:
        console.print(f"  [red]✗ {len(real_errors)} error(s)[/red]")
        for e in real_errors[:5]:
            console.print(f"    {e.contract_fqn}: {escape(e.message)}")
        return f"{len(real_errors)} validation error(s) found."


# ─── Main Chat Loop ─────────────────────────────────────────────────


@click.command("chat")
@click.option("--domain", "-d", default="", help="Domain to chat about")
@click.option(
    "--input",
    "-i",
    "input_dir",
    default="domains/",
    type=click.Path(),
    help="Base directory for contracts (default: domains/)",
)
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
                messages.append(
                    Message(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=response.tool_calls,
                    )
                )
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

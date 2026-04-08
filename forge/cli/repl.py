"""Specora interactive REPL — first-class CLI experience.

Built on prompt_toolkit + Rich. Everything runs in-process.
Slash commands call Python functions directly.
Natural language routes through the LLM agent.

Launch: `spc` or `specora-core` or `python -m forge.cli.repl`
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax
from rich.panel import Panel
from rich.text import Text as RichText
from rich.columns import Columns
from rich.rule import Rule
from rich.tree import Tree
from rich.markdown import Markdown

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

load_dotenv(override=True)

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(force_terminal=True)

# ─── Brand ───────────────────────────────────────────────────────────

LOGO = """[bold magenta]
  ███████╗██████╗ ███████╗ ██████╗ ██████╗ ██████╗  █████╗
  ██╔════╝██╔══██╗██╔════╝██╔════╝██╔═══██╗██╔══██╗██╔══██╗
  ███████╗██████╔╝█████╗  ██║     ██║   ██║██████╔╝███████║
  ╚════██║██╔═══╝ ██╔══╝  ██║     ██║   ██║██╔══██╗██╔══██║
  ███████║██║     ███████╗╚██████╗╚██████╔╝██║  ██║██║  ██║
  ╚══════╝╚═╝     ╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝[/bold magenta]"""

PROMPT_STYLE = Style.from_dict({"prompt": "ansimagenta bold"})

COMMANDS = [
    "/validate", "/compile", "/generate", "/graph",
    "/new", "/add", "/explain", "/refine", "/chat",
    "/heal", "/status", "/tickets", "/history",
    "/visualize", "/migrate", "/extract",
    "/help", "/clear", "/exit",
]

COMMAND_HELP = {
    "/validate [path]":              "Validate contracts against meta-schemas",
    "/compile [path]":               "Compile contracts to IR",
    "/generate [path]":              "Generate code from contracts",
    "/graph [path]":                 "Show dependency graph",
    "/new":                          "Bootstrap a new domain (interview)",
    "/add <kind> -d <domain> -n <name>": "Add a single contract",
    "/explain <path>":               "Explain a contract in plain English",
    "/refine <path> <instruction>":  "Modify a contract via natural language",
    "/chat [--domain <d>]":          "Agentic domain conversation",
    "/heal [path]":                  "Auto-fix validation errors",
    "/status":                       "Healer queue status",
    "/tickets":                      "List healer tickets",
    "/history":                      "Healer fix history",
    "/visualize [path]":             "Generate Mermaid diagrams",
    "/migrate <file> -d <domain>":   "Import from OpenAPI/SQL/Prisma",
    "/extract <path> [--domain <d>]": "Reverse-engineer codebase into contracts",
    "/help":                         "Show this help",
    "/clear":                        "Clear the screen",
    "/exit":                         "Exit the REPL",
    "! <cmd>":                       "Run a shell command",
}

completer = WordCompleter(COMMANDS, sentence=True)


# ─── Output Helpers ──────────────────────────────────────────────────

def _tool(name: str) -> None:
    console.print(f"  [cyan]⚡ {name}[/cyan]")

def _ok(text: str) -> None:
    console.print(f"  [green]✓ {text}[/green]")
    console.print()

def _err(text: str) -> None:
    console.print(Panel(text, border_style="red", title="[red bold]Error[/red bold]", padding=(0, 1)))
    console.print()

def _info(text: str) -> None:
    for line in text.split("\n"):
        console.print(f"  {line}")
    console.print()

def _timed(fn, label: str = "Running"):
    """Run a function with a spinner and return (result, elapsed_seconds)."""
    result = [None]
    error = [None]

    def worker():
        try:
            result[0] = fn()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=worker, daemon=True)
    start = time.time()
    t.start()

    with console.status(f"[magenta]{label}…[/magenta]", spinner="dots"):
        t.join()

    elapsed = time.time() - start

    if error[0]:
        raise error[0]
    return result[0], elapsed


# ─── Welcome Screen ─────────────────────────────────────────────────

def _show_welcome() -> None:
    console.print(LOGO)
    console.print()

    # Domain summary
    domains_dir = Path("domains")
    if domains_dir.exists():
        domains = [d.name for d in domains_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if domains:
            from forge.parser.loader import load_all_contracts
            total_contracts = 0
            domain_info = []
            for d in domains:
                try:
                    contracts = load_all_contracts(domains_dir / d)
                    count = len(contracts)
                    total_contracts += count
                    entities = sum(1 for c in contracts.values() if c.get("kind") == "Entity")
                    domain_info.append(f"[cyan]{d}[/cyan] [dim]({entities} entities, {count} contracts)[/dim]")
                except Exception:
                    domain_info.append(f"[cyan]{d}[/cyan] [dim](error loading)[/dim]")

            console.print(f"  [bold]Domains[/bold]  {' │ '.join(domain_info)}")
            console.print(f"  [bold]Total[/bold]    [dim]{total_contracts} contracts across {len(domains)} domain(s)[/dim]")
        else:
            console.print("  [dim]No domains yet. Run /new to create one or /help for commands.[/dim]")
    else:
        console.print("  [dim]No domains directory. Run /new to get started.[/dim]")

    console.print()
    console.print(Rule(style="dim"))
    console.print()


# ─── Command Handlers ────────────────────────────────────────────────

def cmd_validate(args: str) -> None:
    path = args.strip() or "domains/"
    _tool(f"forge validate {path}")

    from forge.parser.loader import load_all_contracts
    from forge.parser.validator import validate_all
    from forge.error_display import format_errors_rich

    def run():
        contracts = load_all_contracts(Path(path))
        errors = validate_all(contracts)
        return contracts, errors

    (contracts, errors), elapsed = _timed(run, "Validating")

    if not errors:
        _ok(f"All {len(contracts)} contracts are valid [dim]({elapsed:.1f}s)[/dim]")
    else:
        real = [e for e in errors if e.severity == "error"]
        warns = [e for e in errors if e.severity == "warning"]
        console.print(format_errors_rich(errors))
        console.print(f"\n  [dim]{len(contracts)} contracts, {len(real)} error(s), {len(warns)} warning(s) ({elapsed:.1f}s)[/dim]")
        console.print()


def cmd_compile(args: str) -> None:
    path = args.strip() or "domains/"
    _tool(f"forge compile {path}")

    from forge.ir.compiler import Compiler, CompilationError

    try:
        ir, elapsed = _timed(lambda: Compiler(contract_root=Path(path)).compile(), "Compiling")
        _ok(f"{ir.summary()} [dim]({elapsed:.1f}s)[/dim]")
    except CompilationError as e:
        _err("Compilation failed:\n" + "\n".join(str(err) for err in e.errors))


def cmd_generate(args: str) -> None:
    path = args.strip() or "domains/"
    _tool(f"forge generate {path}")

    from forge.ir.compiler import Compiler
    from forge.targets.typescript.gen_types import TypeScriptGenerator
    from forge.targets.fastapi.gen_routes import FastAPIGenerator
    from forge.targets.postgres.gen_ddl import PostgresGenerator

    try:
        def run():
            compiler = Compiler(contract_root=Path(path))
            ir = compiler.compile()
            generators = [TypeScriptGenerator(), FastAPIGenerator(), PostgresGenerator()]
            output_path = Path("runtime/")
            files_written = []
            for gen in generators:
                for f in gen.generate(ir):
                    fp = output_path / f.path
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(f.content, encoding="utf-8")
                    files_written.append(str(f.path))
            return files_written

        files, elapsed = _timed(run, "Generating")

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="green")
        for f in files:
            table.add_row(f"  ✓ {f}")
        console.print(table)
        _ok(f"Generated {len(files)} files in runtime/ [dim]({elapsed:.1f}s)[/dim]")
    except Exception as e:
        _err(str(e))


def cmd_graph(args: str) -> None:
    path = args.strip() or "domains/"
    _tool(f"forge graph {path}")

    from forge.parser.loader import load_all_contracts
    from forge.parser.graph import build_dependency_graph

    contracts = load_all_contracts(Path(path))
    dep_graph = build_dependency_graph(contracts)

    tree = Tree(f"[bold]Contract Graph[/bold] [dim]({len(dep_graph.nodes)} contracts)[/dim]")
    by_kind: dict[str, list] = {}
    for node in dep_graph.nodes.values():
        by_kind.setdefault(node.kind, []).append(node)

    kind_icons = {"Entity": "◆", "Workflow": "◎", "Route": "→", "Page": "▦", "Mixin": "◇", "Agent": "⚙", "Infra": "▣"}
    for kind, nodes in sorted(by_kind.items()):
        icon = kind_icons.get(kind, "•")
        branch = tree.add(f"[bold cyan]{icon} {kind}[/bold cyan] [dim]({len(nodes)})[/dim]")
        for node in sorted(nodes, key=lambda n: n.fqn):
            deps = dep_graph.dependencies_of(node.fqn)
            label = f"[green]{node.fqn}[/green]"
            if deps:
                label += f" [dim]→ {', '.join(d.split('/')[-1] for d in deps)}[/dim]"
            branch.add(label)

    console.print(tree)
    console.print()


def _invoke_click(cmd, argv: list[str]) -> None:
    """Invoke a Click command safely."""
    try:
        ctx = cmd.make_context(cmd.name or "cmd", argv)
        cmd.invoke(ctx)
    except SystemExit:
        pass
    except Exception as e:
        _err(str(e))


def cmd_new(args: str) -> None:
    _tool("factory new")
    from factory.cli.new import factory_new
    _invoke_click(factory_new, [])


def cmd_add(args: str) -> None:
    _tool(f"factory add {args}")
    from factory.cli.add import factory_add
    _invoke_click(factory_add, args.split() if args.strip() else [])


def cmd_explain(args: str) -> None:
    path = args.strip()
    if not path:
        _err("Usage: /explain <path-to-contract>")
        return
    _tool(f"factory explain {path}")
    from factory.cli.explain import factory_explain
    _invoke_click(factory_explain, [path])


def cmd_refine(args: str) -> None:
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        _err("Usage: /refine <path> <instruction>")
        return
    _tool(f"factory refine {parts[0]}")
    from factory.cli.refine import factory_refine
    _invoke_click(factory_refine, parts)


def cmd_chat(args: str) -> None:
    _tool("factory chat")
    from factory.cli.chat import factory_chat
    _invoke_click(factory_chat, args.split() if args.strip() else [])


def cmd_heal(args: str) -> None:
    path = args.strip() or "domains/"
    _tool(f"healer fix {path}")
    from healer.cli.commands import fix
    _invoke_click(fix, [path])


def cmd_status(args: str) -> None:
    _tool("healer status")
    from healer.cli.commands import status
    _invoke_click(status, [])


def cmd_tickets(args: str) -> None:
    _tool("healer tickets")
    from healer.cli.commands import tickets
    _invoke_click(tickets, [])


def cmd_history(args: str) -> None:
    _tool("healer history")
    from healer.cli.commands import history
    _invoke_click(history, [])


def cmd_visualize(args: str) -> None:
    argv = args.split() if args.strip() else ["domains/"]
    _tool(f"factory visualize {' '.join(argv)}")
    from factory.cli.visualize import factory_visualize
    _invoke_click(factory_visualize, argv)


def cmd_extract(args: str) -> None:
    argv = args.split() if args.strip() else []
    if not argv:
        _err("Usage: /extract <path> [--domain <domain>]")
        return
    _tool(f"extract {' '.join(argv)}")
    from extractor.cli.commands import extract
    _invoke_click(extract, argv)


def cmd_migrate(args: str) -> None:
    argv = args.split() if args.strip() else []
    if not argv:
        _err("Usage: /migrate <source-file> --domain <domain>")
        return
    _tool(f"factory migrate {' '.join(argv)}")
    from factory.cli.migrate import factory_migrate
    _invoke_click(factory_migrate, argv)


def cmd_help(args: str) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2), show_edge=False)
    table.add_column(style="cyan bold", min_width=34)
    table.add_column(style="dim")

    # Group commands
    groups = [
        ("Forge", ["/validate [path]", "/compile [path]", "/generate [path]", "/graph [path]"]),
        ("Factory", ["/new", "/add <kind> -d <domain> -n <name>", "/explain <path>",
                     "/refine <path> <instruction>", "/chat [--domain <d>]",
                     "/visualize [path]", "/migrate <file> -d <domain>"]),
        ("Extractor", ["/extract <path> [--domain <d>]"]),
        ("Healer", ["/heal [path]", "/status", "/tickets", "/history"]),
        ("Session", ["/help", "/clear", "/exit", "! <cmd>"]),
    ]

    for group_name, cmds in groups:
        table.add_row(f"[bold white]{group_name}[/bold white]", "")
        for cmd in cmds:
            desc = COMMAND_HELP.get(cmd, "")
            table.add_row(f"  {cmd}", desc)
        table.add_row("", "")

    console.print()
    console.print(Panel(table, title="[bold]Commands[/bold]", border_style="magenta", padding=(1, 2)))
    console.print()


def cmd_shell(cmd: str) -> None:
    _tool(f"$ {cmd}")
    console.print()
    subprocess.run(cmd, shell=True, cwd=os.getcwd())
    console.print()


def cmd_natural(text: str) -> None:
    _tool("Routing via agent…")

    try:
        def run():
            from healer.api.agent import route_natural_language
            return route_natural_language(text)

        result, elapsed = _timed(run, "Thinking")

        command = result.get("command")
        explanation = result.get("explanation", "")

        if not command:
            _info(explanation or "I'm not sure how to help. Try /help.")
            return

        if explanation:
            console.print(f"  [dim italic]{explanation}[/dim italic]")
            console.print()

        parts = command.split(None, 2)
        if len(parts) >= 2:
            handler = ROUTE_MAP.get(f"{parts[0]} {parts[1]}")
            if handler:
                rest = parts[2] if len(parts) > 2 else ""
                handler(rest)
                return

        cmd_shell(f"specora-core {command}")

    except Exception as e:
        _err(str(e))


# ─── Command Router ──────────────────────────────────────────────────

SLASH_MAP = {
    "/validate": cmd_validate, "/compile": cmd_compile,
    "/generate": cmd_generate, "/graph": cmd_graph,
    "/new": cmd_new, "/add": cmd_add, "/explain": cmd_explain,
    "/refine": cmd_refine, "/chat": cmd_chat,
    "/heal": cmd_heal, "/status": cmd_status,
    "/tickets": cmd_tickets, "/history": cmd_history,
    "/visualize": cmd_visualize, "/migrate": cmd_migrate,
    "/extract": cmd_extract,
    "/help": cmd_help,
}

ROUTE_MAP = {
    "forge validate": cmd_validate, "forge compile": cmd_compile,
    "forge generate": cmd_generate, "forge graph": cmd_graph,
    "factory new": cmd_new, "factory add": cmd_add,
    "factory explain": cmd_explain, "factory refine": cmd_refine,
    "factory chat": cmd_chat, "healer fix": cmd_heal,
    "healer status": cmd_status, "healer tickets": cmd_tickets,
    "healer history": cmd_history,
    "factory visualize": cmd_visualize, "factory migrate": cmd_migrate,
    "extract": cmd_extract,
}


def handle_input(text: str) -> bool:
    trimmed = text.strip()
    if not trimmed:
        return True

    if trimmed in ("/exit", "/quit", "exit", "quit"):
        return False

    if trimmed == "/clear":
        os.system("cls" if os.name == "nt" else "clear")
        _show_welcome()
        return True

    if trimmed == "/help":
        cmd_help("")
        return True

    if trimmed.startswith("!"):
        cmd_shell(trimmed[1:].strip())
        return True

    if trimmed.startswith("/"):
        parts = trimmed.split(None, 1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        handler = SLASH_MAP.get(cmd)
        if handler:
            handler(args)
        else:
            _err(f"Unknown command: {cmd}\nType /help for available commands.")
        return True

    cmd_natural(trimmed)
    return True


# ─── Entry Point ─────────────────────────────────────────────────────

def main() -> None:
    _show_welcome()

    history_dir = Path.home() / ".specora"
    history_dir.mkdir(parents=True, exist_ok=True)

    session: PromptSession = PromptSession(
        history=FileHistory(str(history_dir / "history")),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        style=PROMPT_STYLE,
        complete_while_typing=True,
    )

    while True:
        try:
            text = session.prompt(HTML("<prompt>❯ </prompt>"))
            if not handle_input(text):
                break
        except KeyboardInterrupt:
            console.print()
            continue
        except EOFError:
            break

    console.print()
    console.print(Rule(style="dim"))
    console.print("  [dim]Goodbye.[/dim]")
    console.print()


if __name__ == "__main__":
    main()

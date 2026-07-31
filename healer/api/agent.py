"""LLM agent — routes natural language to Specora tools.

Security model
--------------
Everything a model emits here is untrusted input. The user's request routinely
contains contract text, error output, or something pasted from elsewhere, any
of which can carry an instruction aimed at this router rather than at the user.
So the model is never asked *what to run* in a form that can be run: it is
asked to pick from a fixed list, and its answer is checked against that list
before anything is dispatched.

Three properties hold regardless of what the model returns:

1. The reply is matched against :data:`ALLOWED_COMMANDS`. A command that is
   not on the list produces a suggestion for the user to read, never a
   dispatch.
2. Arguments must match :data:`_SAFE_ARGUMENT_RE`, which admits paths and
   flags and nothing else. ``;``, ``&``, ``|``, ``$``, backticks, quotes,
   newlines, and redirection are all rejected outright.
3. The result names a *route key*, not a command line. The REPL dispatches
   that key through an in-process function table. There is no shell on this
   path and no string is ever reassembled into one.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

# Paths, flags, and comma-separated lists. Deliberately no shell metacharacters,
# no quotes, and no whitespace other than the single space that separates
# arguments — an allowlist, so anything new has to be added deliberately.
_SAFE_ARGUMENT_RE = re.compile(r"\A[A-Za-z0-9_.,:@=/+-]*(?: [A-Za-z0-9_.,:@=/+-]+)*\Z")

# Long enough for a path and a couple of flags; short enough that a smuggled
# payload cannot hide in it.
_MAX_ARGUMENT_LENGTH = 300

# A model that has been told the CLI is called "specora" may prefix it.
_INVOCATION_PREFIXES = frozenset({"specora", "specora-core", "spc"})


@dataclass(frozen=True)
class CommandSpec:
    """One command the router is permitted to select."""

    route: str  # Key into the REPL's in-process handler table.
    mutating: bool  # Writes files, spends money, or starts an interview.
    usage: str
    summary: str

    @property
    def words(self) -> tuple[str, ...]:
        """The literal tokens a model reply must begin with."""
        return tuple(self.route.split())


# The single source of truth for what natural-language routing can reach.
# forge.cli.repl asserts its handler table covers exactly these routes.
ALLOWED_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("forge validate", False, "forge validate <path>",
                "Validate contracts against the meta-schemas"),
    CommandSpec("forge compile", False, "forge compile <path>",
                "Compile contracts to IR"),
    CommandSpec("forge generate", True, "forge generate <path>",
                "Generate code from contracts"),
    CommandSpec("forge graph", False, "forge graph <path>",
                "Show the contract dependency graph"),
    CommandSpec("factory new", True, "factory new",
                "Bootstrap a new domain through an interview"),
    CommandSpec("factory add", True, "factory add <kind> --domain <d> --name <n>",
                "Add a single contract"),
    CommandSpec("factory explain", False, "factory explain <path>",
                "Explain a contract in plain English"),
    CommandSpec("factory refine", True, "factory refine <path> <instruction>",
                "Modify a contract from an instruction"),
    CommandSpec("factory chat", True, "factory chat --domain <d>",
                "Agentic domain conversation"),
    CommandSpec("factory visualize", True, "factory visualize <path>",
                "Generate Mermaid diagrams"),
    CommandSpec("factory migrate", True, "factory migrate <file> --domain <d>",
                "Import from OpenAPI/SQL/Prisma"),
    CommandSpec("healer fix", True, "healer fix <path>",
                "Auto-fix validation errors"),
    CommandSpec("healer status", False, "healer status",
                "Show the healer queue"),
    CommandSpec("healer tickets", False, "healer tickets",
                "List healer tickets"),
    CommandSpec("healer history", False, "healer history",
                "Show healer fix history"),
    CommandSpec("extract", True, "extract <path> --domain <d>",
                "Reverse-engineer a codebase into contracts"),
)

_BY_ROUTE = {spec.route: spec for spec in ALLOWED_COMMANDS}

# Longest match first so "forge validate" wins over a hypothetical "forge".
_SPECS_BY_LENGTH = sorted(ALLOWED_COMMANDS, key=lambda s: len(s.words), reverse=True)

ROUTER_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": ["string", "null"],
            "description": "One command from the allowed list, with arguments.",
        },
        "explanation": {
            "type": "string",
            "description": "One sentence describing what the command does.",
        },
    },
    "required": ["command", "explanation"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RouteDecision:
    """The router's verdict. Only ``route`` is safe to act on."""

    status: str  # "routed" | "unroutable" | "rejected" | "unavailable" | "error"
    explanation: str
    route: str | None = None
    args: str = ""
    mutating: bool = False
    # Echoed back for display only. Never parsed, never executed.
    suggestion: str | None = None
    detail: str | None = None

    @property
    def display_command(self) -> str | None:
        """The command line to *show* the user, rebuilt from validated parts."""
        if self.route is None:
            return None
        return f"{self.route} {self.args}".strip()

    def as_dict(self) -> dict:
        """JSON-serialisable view for the ``python -m healer.api.agent`` entry point."""
        return asdict(self)


def command_catalog() -> str:
    """Render the allowlist for the prompt, so the two cannot drift apart."""
    return "\n".join(f"- {spec.usage} — {spec.summary}" for spec in ALLOWED_COMMANDS)


def spec_for(route: str) -> CommandSpec | None:
    """Return the :class:`CommandSpec` for a route key, if it is allowed."""
    return _BY_ROUTE.get(route)


def validate_command(raw: str) -> tuple[CommandSpec | None, str, str | None]:
    """Check a model-proposed command against the allowlist.

    Args:
        raw: The command string the model emitted.

    Returns:
        ``(spec, args, reason)``. ``spec`` is ``None`` when the command is not
        permitted, in which case ``reason`` says why.
    """
    if not isinstance(raw, str):
        return None, "", f"expected a string command, got {type(raw).__name__}"

    candidate = raw.strip()
    if not candidate:
        return None, "", "empty command"

    # Reject before tokenising: a control character or metacharacter anywhere
    # in the string means the reply is not a plain command, whatever it parses
    # to afterwards.
    if any(ch in candidate for ch in ";&|<>$`\\\n\r\t\"'()*?!{}[]#~"):
        return None, "", "command contains shell metacharacters"

    tokens = candidate.split()
    if tokens and tokens[0].lower() in _INVOCATION_PREFIXES:
        tokens = tokens[1:]

    for spec in _SPECS_BY_LENGTH:
        n = len(spec.words)
        if tuple(t.lower() for t in tokens[:n]) != spec.words:
            continue
        args = " ".join(tokens[n:])
        if len(args) > _MAX_ARGUMENT_LENGTH:
            return None, "", "arguments too long"
        if not _SAFE_ARGUMENT_RE.match(args):
            return None, "", "arguments contain unsupported characters"
        return spec, args, None

    return None, "", "not an allowed command"


def route_natural_language(user_input: str) -> RouteDecision:
    """Ask the model which allowlisted command matches *user_input*.

    Never returns anything executable that did not survive
    :func:`validate_command`.
    """
    # Imported lazily: the REPL starts without the [llm] extra installed, and
    # only natural-language input needs it.
    try:
        from engine.config import EngineConfigError
        from engine.engine import LLMEngine
        from engine.prompts import get_prompt
        from engine.structured import StructuredOutputError
    except ImportError as exc:
        return RouteDecision(
            status="unavailable",
            explanation=f"LLM support is not installed: {exc}",
            detail="ImportError",
        )

    try:
        engine = LLMEngine.from_env()
    except EngineConfigError as exc:
        return RouteDecision(
            status="unavailable",
            explanation=f"No LLM provider configured: {exc}",
            detail="EngineConfigError",
        )

    prompt = get_prompt("cli_router")
    system = prompt.render(commands=command_catalog())

    try:
        payload = engine.ask_json(
            f"User request: {user_input}",
            schema=ROUTER_RESPONSE_SCHEMA,
            system=system,
            purpose="cli_routing",
        )
    except StructuredOutputError as exc:
        # The real cause matters: prose back from the router means the prompt
        # or the model changed, not that the user asked for something odd.
        logger.warning("Router returned unparsable output: %s", exc)
        return RouteDecision(
            status="error",
            explanation="The router did not return a usable answer.",
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Routing call failed")
        return RouteDecision(
            status="error",
            explanation=f"Routing failed: {type(exc).__name__}: {exc}",
            detail=type(exc).__name__,
        )

    explanation = str(payload.get("explanation") or "").strip()
    raw_command = payload.get("command")

    if raw_command is None:
        return RouteDecision(
            status="unroutable",
            explanation=explanation or "No Specora command matches that request.",
        )

    spec, args, reason = validate_command(raw_command)
    if spec is None:
        logger.warning(
            "Rejected routed command (%s): %r", reason, str(raw_command)[:200]
        )
        return RouteDecision(
            status="rejected",
            explanation=explanation or "That does not map to a Specora command.",
            suggestion=str(raw_command)[:200],
            detail=reason,
        )

    return RouteDecision(
        status="routed",
        explanation=explanation or spec.summary,
        route=spec.route,
        args=args,
        mutating=spec.mutating,
    )


def main() -> None:
    """CLI entry point for agent routing. Reads argv, writes JSON to stdout."""
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "explanation": "Usage: python -m healer.api.agent 'user input'",
        }))
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])
    print(json.dumps(route_natural_language(user_input).as_dict()))


if __name__ == "__main__":
    main()

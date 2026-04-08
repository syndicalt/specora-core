"""LLM agent — routes natural language to Specora tools."""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TOOLS_DESCRIPTION = """You are the Specora CLI agent. The user types natural language and you determine which command to run.

Available commands:
- forge validate <path> — Validate contracts (default path: domains/)
- forge compile <path> — Compile contracts to IR
- forge generate <path> — Generate code from contracts
- forge graph <path> — Show dependency graph
- factory new — Create a new domain via interview
- factory add <kind> --domain <d> --name <n> — Add a single contract
- factory explain <path> — Explain a contract in English
- factory refine <path> "<instruction>" — Modify a contract
- healer fix <path> — Auto-fix validation errors
- healer status — Show healer queue
- healer tickets — List healer tickets
- healer approve <id> — Approve a fix
- healer history — Show fix history
- diff history <fqn> — Show contract change history
- init <domain> — Create a new domain scaffold

Given the user's request, respond with ONLY a JSON object:
{"command": "<the specora CLI command to run>", "explanation": "<brief explanation of what you're doing>"}

If the request doesn't map to any command, respond:
{"command": null, "explanation": "<helpful message about what commands are available>"}
"""


def route_natural_language(user_input: str) -> dict:
    """Use LLM to route natural language to a Specora command.

    Returns dict with 'command' (str or None) and 'explanation' (str).
    """
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception as e:
        return {"command": None, "explanation": f"LLM not available: {e}"}

    try:
        response = engine.ask(
            question=f"User request: {user_input}",
            system=TOOLS_DESCRIPTION,
        )

        # Parse JSON from response
        match = re.search(r'\{[^}]+\}', response)
        if match:
            return json.loads(match.group())
        return {"command": None, "explanation": response[:200]}

    except Exception as e:
        return {"command": None, "explanation": f"Error: {e}"}


def main():
    """CLI entry point for agent routing. Reads from stdin, writes to stdout."""
    if len(sys.argv) < 2:
        print(json.dumps({"command": None, "explanation": "Usage: python -m healer.api.agent 'user input'"}))
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])
    result = route_natural_language(user_input)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

"""Interview framework — the conversational loop that powers the Factory.

An Interview is a multi-turn conversation between the user and an LLM.
The framework handles:
  - Sending user input to the LLM with contract-aware system prompts
  - Parsing structured data from LLM responses
  - Maintaining conversation history
  - Integrating with session persistence for resume
  - Styled terminal UI with bordered input panels

Usage:
    from factory.interviews.base import Interview
    from engine.engine import LLMEngine

    interview = Interview(engine, "entity_interview", domain="veterinary")
    answer = interview.ask_user("What entities does your system have?")
    structured = interview.ask_llm_structured(answer, "Parse these into fields")
"""

from __future__ import annotations

import json
import logging
from typing import Any

import yaml
from rich.console import Console

from engine.engine import LLMEngine
from engine.providers.base import Message

logger = logging.getLogger(__name__)
console = Console()


def _render_prompt(
    prompt: str,
    mode: str = "",
    domain: str = "",
    confirm: bool = False,
) -> str:
    """Render a question as output, then a ruled input prompt below.

    Output scrolls above. Input is between two horizontal rules
    with a context line. Clean and minimal, like Claude Code.

    Layout:
        What fields does a task have?
        List the data you want to store, comma-separated.
        Example: name, email, status, priority, due date

        ─────────────────────────────────────────────────────
        entity interview | todolist
        > user types here_
        ─────────────────────────────────────────────────────
    """
    # Print the question as regular output
    console.print()
    for line in prompt.split("\n"):
        console.print(f"  {line}")

    # Context line + ruled input
    context_parts = []
    if mode:
        context_parts.append(mode)
    if domain:
        context_parts.append(domain)
    context = " | ".join(context_parts)

    console.print()
    if context:
        console.rule(f"[dim]{context}[/dim]", style="dim", align="right")
    else:
        console.rule(style="dim")
    if confirm:
        response = console.input("[bold yellow]>[/bold yellow] [dim](Y/n)[/dim] ").strip()
    else:
        response = console.input("[bold green]>[/bold green] ").strip()
    console.rule(style="dim")
    console.print()
    console.print()

    return response


class Interview:
    """A multi-turn conversation between user and LLM.

    The interview manages the conversation flow: prompting the user
    with styled panels, sending their input to the LLM, and extracting
    structured data.

    Attributes:
        engine: The LLM engine for AI responses.
        system_prompt: System prompt that sets the LLM's role.
        history: Conversation history for context continuity.
        mode: Current interview phase (displayed in input panel).
        domain: Current domain name (displayed in input panel).
    """

    def __init__(
        self,
        engine: LLMEngine,
        system_prompt: str = "",
        mode: str = "",
        domain: str = "",
    ):
        self.engine = engine
        self.system_prompt = system_prompt
        self.history: list[Message] = []
        self.mode = mode
        self.domain = domain

    def ask_user(self, prompt: str) -> str:
        """Display a styled input box and get user input.

        The cursor appears inside the bordered box.

        Args:
            prompt: The question to display (plain text with newlines).

        Returns:
            The user's text input.
        """
        # Strip Rich markup for the box (box uses manual rendering)
        clean_prompt = prompt.replace("[dim]", "").replace("[/dim]", "")
        response = _render_prompt(
            clean_prompt,
            mode=self.mode,
            domain=self.domain,
        )
        self.history.append(Message(role="user", content=response))
        return response

    def show(self, message: str) -> None:
        """Display a message to the user (from the Factory)."""
        console.print(f"  {message}")

    def ask_llm(self, user_input: str, instruction: str = "") -> str:
        """Send user input to the LLM and get a text response.

        Args:
            user_input: What the user said.
            instruction: Additional instruction appended to the user message.

        Returns:
            The LLM's text response.
        """
        full_input = user_input
        if instruction:
            full_input += f"\n\n[Internal instruction: {instruction}]"

        messages = list(self.history)
        messages.append(Message(role="user", content=full_input))

        try:
            response = self.engine.chat(
                messages=messages,
                system=self.system_prompt,
                temperature=0.3,
            )
        except Exception as e:
            raise InterviewLLMError(f"LLM request failed: {e}") from e

        self.history.append(Message(role="assistant", content=response.content))
        return response.content

    def ask_llm_structured(self, user_input: str, instruction: str) -> dict:
        """Ask the LLM and parse the response as YAML or JSON.

        The LLM is instructed to respond with structured data.
        Tries YAML parsing first, then JSON.

        Args:
            user_input: What the user said.
            instruction: What structure we want back.

        Returns:
            Parsed dict from the LLM's response.

        Raises:
            InterviewParseError: If the response can't be parsed.
        """
        full_instruction = (
            f"{instruction}\n\n"
            "Respond with ONLY valid YAML (no markdown fences, no explanation). "
            "Start your response with the YAML directly."
        )
        raw = self.ask_llm(user_input, full_instruction)

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            result = yaml.safe_load(cleaned)
            if isinstance(result, dict):
                return result
        except yaml.YAMLError:
            pass

        try:
            result = json.loads(cleaned)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        raise InterviewParseError(
            f"Could not parse LLM response as YAML or JSON:\n{raw[:500]}"
        )

    def confirm(self, message: str) -> bool:
        """Ask a yes/no confirmation with styled input box.

        Args:
            message: The confirmation prompt.

        Returns:
            True if user confirms, False otherwise.
        """
        response = _render_prompt(
            message,
            mode=self.mode,
            domain=self.domain,
            confirm=True,
        )
        return response.lower() in ("", "y", "yes")


class InterviewParseError(Exception):
    """Raised when an LLM response cannot be parsed into structured data."""

    pass


class InterviewLLMError(Exception):
    """Raised when the LLM API request fails (rate limit, auth, network, etc.)."""

    pass

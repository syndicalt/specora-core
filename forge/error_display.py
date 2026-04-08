"""Human-readable validation error formatting.

Transforms cryptic regex-mismatch errors into actionable messages
with suggested fixes. Shared by the CLI validate command, Factory
inline errors, and Healer reports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from forge.normalize import normalize_graph_edge, normalize_name, normalize_fqn
from forge.parser.validator import ContractValidationError


@dataclass
class FormattedError:
    """A validation error with human-readable message and suggested fix."""

    fqn: str
    path: str
    message: str
    suggestion: str
    severity: str = "error"


# Known regex patterns and their human-readable translations
_PATTERN_HINTS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"does not match '\^\[a-z\]\[a-z0-9_\]\*\$'"),
        "must be snake_case (lowercase with underscores)",
        "name",
    ),
    (
        re.compile(
            r"does not match '\^\(entity\|workflow\|page\|route\|agent\|mixin\|infra\)/\[a-z\]\[a-z0-9_/\]\*\$'"
        ),
        "must be a fully qualified name (kind/domain/name, all lowercase)",
        "fqn",
    ),
    (
        re.compile(r"does not match '\^\[A-Z\]\[A-Z0-9_\]\*\$'"),
        "must be SCREAMING_SNAKE_CASE (all uppercase with underscores)",
        "graph_edge",
    ),
    (
        re.compile(r"does not match '\^\[A-Z\]\{2,6\}\$'"),
        "must be 2-6 uppercase letters (e.g., INC, BOOK, TASK)",
        "prefix",
    ),
]


def humanize_error(err: ContractValidationError) -> FormattedError:
    """Convert a raw validation error into a human-readable format."""
    message = err.message
    suggestion = ""

    # Try to extract the offending value from the message
    value_match = re.search(r"'([^']+)' does not match", message)
    offending_value = value_match.group(1) if value_match else ""

    for pattern, hint, fix_type in _PATTERN_HINTS:
        if pattern.search(message):
            if fix_type == "name" and offending_value:
                fixed = normalize_name(offending_value)
                suggestion = f"Use '{fixed}' instead of '{offending_value}'"
                message = f"'{offending_value}' {hint}"
            elif fix_type == "fqn" and offending_value:
                # Infer kind from path context
                kind = _infer_kind_from_path(err.path)
                fixed = normalize_fqn(offending_value, kind, "")
                suggestion = f"Use '{fixed}' instead of '{offending_value}'"
                message = f"'{offending_value}' {hint}"
            elif fix_type == "graph_edge" and offending_value:
                fixed = normalize_graph_edge(offending_value)
                suggestion = f"Use '{fixed}' instead of '{offending_value}'"
                message = f"'{offending_value}' {hint}"
            elif fix_type == "prefix" and offending_value:
                suggestion = f"Use 2-6 uppercase letters (e.g., '{offending_value[:4].upper()}')"
                message = f"'{offending_value}' {hint}"
            else:
                message = f"{offending_value or 'Value'} {hint}"
            break

    return FormattedError(
        fqn=err.contract_fqn,
        path=err.path,
        message=message,
        suggestion=suggestion,
        severity=err.severity,
    )


def _infer_kind_from_path(path: str) -> str:
    """Guess the contract kind from the error path context."""
    if "state_machine" in path:
        return "workflow"
    if "mixins" in path:
        return "mixin"
    if "references.entity" in path:
        return "entity"
    return "entity"


def format_errors_rich(errors: list[ContractValidationError]) -> str:
    """Format validation errors as a Rich-renderable string.

    Groups by contract, adds suggestions, uses color.
    """
    if not errors:
        return "[green]No errors[/green]"

    formatted = [humanize_error(e) for e in errors]

    # Group by FQN
    by_fqn: dict[str, list[FormattedError]] = {}
    for fe in formatted:
        by_fqn.setdefault(fe.fqn, []).append(fe)

    lines: list[str] = []
    for fqn, errs in by_fqn.items():
        lines.append(f"\n[bold]{fqn}[/bold]")
        for fe in errs:
            icon = "[red]x[/red]" if fe.severity == "error" else "[yellow]![/yellow]"
            lines.append(f"  {icon} [dim]{fe.path}:[/dim] {fe.message}")
            if fe.suggestion:
                lines.append(f"    [green]fix:[/green] {fe.suggestion}")

    error_count = sum(1 for e in errors if e.severity == "error")
    warn_count = sum(1 for e in errors if e.severity == "warning")
    lines.append(f"\n[bold]{error_count} error(s), {warn_count} warning(s)[/bold]")
    return "\n".join(lines)

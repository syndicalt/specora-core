"""Error classification — assign tier, type, and priority to errors."""
from __future__ import annotations

import re
from dataclasses import dataclass

from forge.parser.validator import ContractValidationError
from healer.models import Priority


@dataclass
class Classification:
    """Result of classifying an error."""
    error_type: str
    tier: int
    priority: Priority
    fixable_by: str = "contract"  # "contract" | "generator" | "data" | "unknown"


# Tier 1 (deterministic) validation error patterns
_TIER1_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"does not match '\^\[a-z\]\[a-z0-9_\]\*\$'"), "naming"),
    (re.compile(r"does not match '\^\(entity\|workflow"), "fqn_format"),
    (re.compile(r"does not match '\^\[A-Z\]\[A-Z0-9_\]\*\$'"), "graph_edge"),
    (re.compile(r"does not match '\^\[A-Z\]\{2,6\}\$'"), "number_prefix"),
]

# Tier 2 (structural) validation error patterns
_TIER2_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"is a required property"), "missing_field"),
    (re.compile(r"is not valid under any of the given schemas"), "schema_mismatch"),
    (re.compile(r"is not one of"), "invalid_enum"),
]


def classify_validation_error(err: ContractValidationError) -> Classification:
    msg = err.message
    for pattern, error_type in _TIER1_PATTERNS:
        if pattern.search(msg):
            return Classification(error_type=error_type, tier=1, priority=Priority.HIGH)
    for pattern, error_type in _TIER2_PATTERNS:
        if pattern.search(msg):
            return Classification(error_type=error_type, tier=2, priority=Priority.HIGH)
    return Classification(error_type="structural", tier=2, priority=Priority.MEDIUM)


# Runtime errors that indicate a generator bug (not a contract issue)
_GENERATOR_BUG_PATTERNS = [
    re.compile(r"invalid UUID", re.IGNORECASE),
    re.compile(r"invalid input for query argument", re.IGNORECASE),
    re.compile(r"column .* does not exist", re.IGNORECASE),
    re.compile(r"relation .* does not exist", re.IGNORECASE),
    re.compile(r"syntax error at or near", re.IGNORECASE),
    re.compile(r"ImportError|ModuleNotFoundError", re.IGNORECASE),
    re.compile(r"NameError: name .* is not defined", re.IGNORECASE),
    re.compile(r"AttributeError", re.IGNORECASE),
]

# Runtime errors that indicate a data issue (not a contract or generator issue)
_DATA_ISSUE_PATTERNS = [
    re.compile(r"duplicate key value violates unique constraint", re.IGNORECASE),
    re.compile(r"foreign key.*violates", re.IGNORECASE),
    re.compile(r"null value in column .* violates not-null", re.IGNORECASE),
    re.compile(r"value too long for type", re.IGNORECASE),
    re.compile(r"connection refused|connection reset|timeout", re.IGNORECASE),
]


def _infer_fixable_by(error: str) -> str:
    """Determine whether a runtime error is fixable by contract, generator, or data change."""
    for pattern in _GENERATOR_BUG_PATTERNS:
        if pattern.search(error):
            return "generator"
    for pattern in _DATA_ISSUE_PATTERNS:
        if pattern.search(error):
            return "data"
    return "contract"


def classify_raw_error(source: str, error: str, context: dict) -> Classification:
    if source == "runtime":
        status = context.get("status_code", 0)
        fixable_by = _infer_fixable_by(error)
        if status >= 500:
            return Classification(error_type="runtime_500", tier=3, priority=Priority.CRITICAL, fixable_by=fixable_by)
        return Classification(error_type="runtime_exception", tier=3, priority=Priority.HIGH, fixable_by=fixable_by)
    if source == "compilation":
        if "unresolved reference" in error.lower():
            return Classification(error_type="missing_reference", tier=2, priority=Priority.HIGH)
        if "cycle" in error.lower():
            return Classification(error_type="dependency_cycle", tier=2, priority=Priority.CRITICAL)
        return Classification(error_type="compilation_error", tier=2, priority=Priority.HIGH)
    return Classification(error_type="unknown", tier=2, priority=Priority.MEDIUM, fixable_by="unknown")

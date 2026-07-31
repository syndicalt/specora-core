"""Data models for codebase analysis and extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from forge.normalize import normalize_name

# The meta-schemas pin contract and domain names to this shape. Extracted names
# come from a codebase the user did not write, so they reach the emitter as
# arbitrary text: a class name via `ast`, or — on the LLM path — a string an
# attacker can choose outright. `normalize_name` only recases; it leaves `/`
# and `..` intact, which is enough to walk a write out of the output root.
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def safe_contract_name(raw: str, *, fallback: str = "extracted") -> str:
    """Coerce an extracted name into a legal contract/field identifier.

    Guarantees the result matches `^[a-z][a-z0-9_]*$`, so it is safe both as a
    meta-schema `metadata.name` and as a single path segment.
    """
    candidate = normalize_name(raw)
    candidate = re.sub(r"[^a-z0-9_]+", "_", candidate.lower())
    candidate = re.sub(r"_{2,}", "_", candidate).strip("_")
    if not candidate:
        return fallback
    if candidate[0].isdigit():
        candidate = f"{fallback}_{candidate}"
    return candidate if _SAFE_NAME.match(candidate) else fallback


# Names that mark a field as a credential. Without `sensitive: true` the field
# is built into the generated response model, so the API publishes it.
_SENSITIVE = re.compile(
    r"(^|_)("
    r"password|passwd|pwd|secret|api_key|apikey|private_key|access_key|"
    r"refresh_token|access_token|auth_token|session_token|token|credential|"
    r"credentials|salt|otp|ssn|cvv|card_number|security_answer"
    r")(_|$)"
)

# Suffixes that turn a credential-looking name into metadata about a
# credential. `token_count` and `password_expires_at` are not themselves
# secrets.
_NOT_SENSITIVE = re.compile(
    r"_(count|expires_at|expires|expiry|ttl|type|kind|url|uri|at|id|version|"
    r"algo|algorithm|required|enabled|rotated_at|last_used_at)$"
)


# Mixins every extracted entity gets, and the fields they already declare.
# Redeclaring one of these on the entity is a compilation error whenever the
# scanned type disagrees with the mixin's, which it usually does (`id: str`
# against the mixin's `id: uuid`).
DEFAULT_MIXINS = ("mixin/stdlib/timestamped", "mixin/stdlib/identifiable")

STDLIB_MIXIN_FIELDS: dict[str, tuple[str, ...]] = {
    "mixin/stdlib/timestamped": ("created_at", "updated_at"),
    "mixin/stdlib/identifiable": ("id", "number"),
    "mixin/stdlib/auditable": ("created_at", "updated_at", "created_by", "updated_by"),
    "mixin/stdlib/taggable": ("tags",),
    "mixin/stdlib/commentable": ("comments",),
    "mixin/stdlib/soft_deletable": ("deleted_at", "deleted_by", "is_deleted"),
}


def is_sensitive_name(name: str) -> bool:
    """Whether a field name denotes a credential that must not be serialised."""
    lowered = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    return bool(_SENSITIVE.search(lowered)) and not _NOT_SENSITIVE.search(lowered)


class FileRole(str, Enum):
    MODEL = "model"
    ROUTE = "route"
    PAGE = "page"
    MIGRATION = "migration"
    CONFIG = "config"
    TEST = "test"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class FileClassification:
    path: str
    role: FileRole
    language: str
    size_bytes: int = 0


@dataclass
class ExtractedField:
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    enum_values: list[str] = field(default_factory=list)
    reference_entity: str = ""
    # Empty until the cross-reference pass confirms the target entity has this
    # field; the compiler rejects a `display` that names a missing field.
    reference_display: str = ""
    reference_edge: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = False
    immutable: bool = False


@dataclass
class ExtractedEntity:
    name: str
    source_file: str
    fields: list[ExtractedField] = field(default_factory=list)
    description: str = ""
    confidence: Confidence = Confidence.HIGH
    mixins: list[str] = field(default_factory=list)
    state_field: str = ""
    state_values: list[str] = field(default_factory=list)

    def to_emitter_data(self) -> dict:
        """Convert to the dict format expected by emit_entity()."""
        mixins = list(self.mixins) if self.mixins else list(DEFAULT_MIXINS)
        provided_by_mixins = {
            name for mixin in mixins for name in STDLIB_MIXIN_FIELDS.get(mixin, ())
        }

        fields: dict[str, dict[str, Any]] = {}
        for f in self.fields:
            name = safe_contract_name(f.name, fallback="")
            if not name or name in fields:
                continue
            if name in provided_by_mixins:
                # The mixin already declares this field, and the compiler
                # rejects an entity that redeclares it with a different type —
                # a scanned `id: str` against the mixin's `id: uuid`.
                continue

            fd: dict[str, Any] = {"type": f.type}
            if f.required:
                fd["required"] = True
            if f.immutable:
                fd["immutable"] = True
            # Re-checked here rather than trusted from the analyzer: this is the
            # last point before the field becomes part of a published API.
            if f.sensitive or is_sensitive_name(name):
                fd["sensitive"] = True
            if f.description:
                fd["description"] = f.description
            if f.enum_values:
                fd["enum"] = f.enum_values
            if f.constraints:
                fd["constraints"] = dict(f.constraints)
            if f.reference_entity:
                edge = f.reference_edge or re.sub(r"_ID$", "", name.upper())
                references = {
                    "entity": f.reference_entity,
                    "graph_edge": edge or "REFERENCES",
                }
                # `display` names a field on the *target*. The compiler rejects
                # one the target does not have, so it is only emitted when the
                # cross-reference pass confirmed it exists.
                if f.reference_display:
                    references["display"] = f.reference_display
                fd["references"] = references
            fields[name] = fd

        return {
            "description": self.description or f"A {self.name} entity",
            "fields": fields,
            "mixins": mixins,
        }


@dataclass
class ExtractedRoute:
    path: str
    method: str
    entity_name: str
    source_file: str
    summary: str = ""
    confidence: Confidence = Confidence.HIGH


@dataclass
class ExtractedWorkflow:
    name: str
    entity_name: str
    states: list[str]
    initial: str
    source_file: str
    transitions: dict[str, list[str]] = field(default_factory=dict)
    confidence: Confidence = Confidence.MEDIUM

    def to_emitter_data(self) -> dict:
        """Convert to the dict format expected by emit_workflow().

        `spec.transitions` is a map of source state to reachable states. It was
        previously emitted as a list of `{from, to}` pairs, which is not what
        `workflow.meta.yaml` declares, so every extraction that detected a state
        machine produced a domain that failed `validate_all`.
        """
        ordered = [safe_contract_name(s, fallback="") for s in self.states]
        ordered = [s for s in dict.fromkeys(ordered) if s]
        if not ordered:
            ordered = ["active"]

        states: dict[str, dict[str, Any]] = {
            s: {"label": s.replace("_", " ").title()} for s in ordered
        }

        transitions: dict[str, list[str]] = {}
        for source, targets in self.transitions.items():
            src = safe_contract_name(source, fallback="")
            named = (safe_contract_name(x, fallback="") for x in targets)
            valid = [t for t in named if t in states]
            if src in states and valid:
                transitions[src] = list(dict.fromkeys(valid))

        if not transitions and len(ordered) > 1:
            # No transition table was recovered from the source. A declared
            # order is the only signal available, so chain it — and mark the
            # chain's end terminal, because the compiler rejects a state with
            # no outgoing transitions that is not declared as such.
            # Pairwise over a sequence: the tail is one shorter by construction.
            for a, b in zip(ordered, ordered[1:], strict=False):
                transitions[a] = [b]

        for name in states:
            if name not in transitions:
                states[name]["terminal"] = True

        initial = safe_contract_name(self.initial, fallback="")
        if initial not in states:
            initial = ordered[0]

        return {
            "initial": initial,
            "states": states,
            "transitions": transitions,
            "description": f"{self.entity_name} lifecycle",
        }


@dataclass
class AnalysisReport:
    domain: str
    entities: list[ExtractedEntity] = field(default_factory=list)
    routes: list[ExtractedRoute] = field(default_factory=list)
    workflows: list[ExtractedWorkflow] = field(default_factory=list)
    files_scanned: int = 0
    files_analyzed: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.entities:
            parts.append(f"{len(self.entities)} entities")
        if self.routes:
            parts.append(f"{len(self.routes)} routes")
        if self.workflows:
            parts.append(f"{len(self.workflows)} workflows")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")
        return ", ".join(parts) if parts else "nothing found"

"""Data models for codebase analysis and extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


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
    reference_display: str = "name"
    reference_edge: str = ""


@dataclass
class ExtractedEntity:
    name: str
    source_file: str
    fields: list[ExtractedField] = field(default_factory=list)
    description: str = ""
    confidence: Confidence = Confidence.HIGH
    mixins: list[str] = field(default_factory=list)
    has_timestamps: bool = True
    state_field: str = ""
    state_values: list[str] = field(default_factory=list)

    def to_emitter_data(self) -> dict:
        """Convert to the dict format expected by emit_entity()."""
        fields: dict[str, dict[str, Any]] = {}
        for f in self.fields:
            fd: dict[str, Any] = {"type": f.type}
            if f.required:
                fd["required"] = True
            if f.description:
                fd["description"] = f.description
            if f.enum_values:
                fd["enum"] = f.enum_values
            if f.reference_entity:
                fd["references"] = {
                    "entity": f.reference_entity,
                    "display": f.reference_display,
                    "graph_edge": f.reference_edge or f.name.upper().replace("_ID", ""),
                }
            fields[f.name] = fd

        mixins = list(self.mixins) if self.mixins else []
        if not mixins:
            mixins = ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"]

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
    transitions: list[dict] = field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM

    def to_emitter_data(self) -> dict:
        """Convert to the dict format expected by emit_workflow()."""
        states = {}
        for s in self.states:
            states[s] = {"label": s.replace("_", " ").title()}

        transitions = self.transitions if self.transitions else []
        if not transitions and len(self.states) > 1:
            for i in range(len(self.states) - 1):
                transitions.append({"from": self.states[i], "to": self.states[i + 1]})

        return {
            "initial": self.initial,
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

    def summary(self) -> str:
        parts = []
        if self.entities:
            parts.append(f"{len(self.entities)} entities")
        if self.routes:
            parts.append(f"{len(self.routes)} routes")
        if self.workflows:
            parts.append(f"{len(self.workflows)} workflows")
        return ", ".join(parts) if parts else "nothing found"

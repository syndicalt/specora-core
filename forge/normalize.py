"""Deterministic contract normalization.

Transforms contract dicts so they conform to meta-schema patterns:
  - metadata.name       → snake_case  (^[a-z][a-z0-9_]*$)
  - requires[]          → FQN format  (kind/domain/name, all lowercase)
  - references.entity   → FQN format
  - references.graph_edge → SCREAMING_SNAKE_CASE (^[A-Z][A-Z0-9_]*$)
  - spec.state_machine  → FQN format
  - spec.entity         → FQN format (Route/Page contracts)

Shared by Factory emitters (auto-apply) and Healer (show diff).
"""
from __future__ import annotations

import re


# ── Name normalization ──────────────────────────────────────────────────

_PASCAL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def normalize_name(name: str) -> str:
    """Convert any casing to snake_case.

    PascalCase  → snake_case:  TodoList → todo_list
    camelCase   → snake_case:  todoList → todo_list
    Mixed_Case  → snake_case:  Task_lifecycle → task_lifecycle
    Already ok  → unchanged:   task → task
    """
    # Split on PascalCase/camelCase boundaries
    parts = _PASCAL_SPLIT.sub("_", name).lower()
    # Collapse multiple underscores
    return re.sub(r"_+", "_", parts).strip("_")


# ── FQN normalization ───────────────────────────────────────────────────

_VALID_KINDS = {"entity", "workflow", "page", "route", "agent", "mixin", "infra"}


def normalize_fqn(ref: str, default_kind: str, domain: str) -> str:
    """Expand a short-form ref to a fully qualified name.

    Short-form refs from LLM output:
      "todo_list/User"                → "entity/todo_list/user"
      "workflow/todo_list/Task_lifecycle" → "workflow/todo_list/task_lifecycle"

    Already-valid FQNs are still lowercased:
      "entity/todo_list/User"         → "entity/todo_list/user"

    Args:
        ref: The reference string to normalize.
        default_kind: Kind to prepend if missing (e.g., "entity").
        domain: Domain to use if the ref has no domain segment.
    """
    parts = ref.split("/")

    if len(parts) >= 3 and parts[0] in _VALID_KINDS:
        # Already has kind prefix — just lowercase the name parts
        kind = parts[0]
        rest = "/".join(normalize_name(p) for p in parts[1:])
        return f"{kind}/{rest}"

    if len(parts) == 2:
        # domain/Name format — prepend default_kind
        ref_domain = normalize_name(parts[0])
        ref_name = normalize_name(parts[1])
        return f"{default_kind}/{ref_domain}/{ref_name}"

    if len(parts) == 1:
        # Just a name — prepend kind and domain
        return f"{default_kind}/{normalize_name(domain)}/{normalize_name(parts[0])}"

    # Fallback: lowercase everything and prepend kind
    rest = "/".join(normalize_name(p) for p in parts)
    return f"{default_kind}/{rest}"


# ── Graph edge normalization ────────────────────────────────────────────


def normalize_graph_edge(edge: str) -> str:
    """Convert any casing to SCREAMING_SNAKE_CASE.

    assigned_to → ASSIGNED_TO
    AssignedTo  → ASSIGNED_TO
    ASSIGNED_TO → ASSIGNED_TO (unchanged)
    """
    # First convert PascalCase/camelCase to snake_case, then uppercase
    return normalize_name(edge).upper()


# ── Contract-level normalization ────────────────────────────────────────

# Map contract kind to the default kind for reference resolution
_KIND_TO_REF_KIND = {
    "Entity": "entity",
    "Workflow": "workflow",
    "Page": "page",
    "Route": "route",
    "Agent": "agent",
    "Mixin": "mixin",
    "Infra": "infra",
}


def normalize_contract(contract: dict) -> dict:
    """Normalize an entire contract dict in-place and return it.

    Fixes all known pattern violations:
    1. metadata.name → snake_case
    2. requires[] → FQN format
    3. spec.fields.*.references.entity → FQN format
    4. spec.fields.*.references.graph_edge → SCREAMING_SNAKE_CASE
    5. spec.state_machine → FQN format
    6. spec.entity → FQN format (Route/Page)
    7. spec.mixins[] → FQN format
    """
    kind = contract.get("kind", "Entity")
    metadata = contract.get("metadata", {})
    domain = metadata.get("domain", "")
    spec = contract.get("spec", {})

    # 1. Normalize metadata.name
    if "name" in metadata:
        metadata["name"] = normalize_name(metadata["name"])

    # Normalize domain too
    if "domain" in metadata:
        metadata["domain"] = normalize_name(metadata["domain"])

    # 2. Normalize requires array
    requires = contract.get("requires", [])
    if requires:
        contract["requires"] = [
            _normalize_requires_entry(r, domain) for r in requires
        ]

    # 3-4. Normalize field references
    fields = spec.get("fields", {})
    for field_def in fields.values():
        refs = field_def.get("references")
        if refs:
            if "entity" in refs:
                refs["entity"] = normalize_fqn(refs["entity"], "entity", domain)
            if "graph_edge" in refs:
                refs["graph_edge"] = normalize_graph_edge(refs["graph_edge"])

    # 5. Normalize state_machine FQN
    if "state_machine" in spec:
        spec["state_machine"] = normalize_fqn(
            spec["state_machine"], "workflow", domain
        )

    # 6. Normalize spec.entity (Route/Page contracts)
    if "entity" in spec:
        spec["entity"] = normalize_fqn(spec["entity"], "entity", domain)

    # 7. Normalize mixins
    mixins = spec.get("mixins", [])
    if mixins:
        spec["mixins"] = [normalize_fqn(m, "mixin", domain) for m in mixins]

    return contract


def _normalize_requires_entry(ref: str, domain: str) -> str:
    """Normalize a single requires entry, inferring kind from context."""
    parts = ref.split("/")

    # If already has a valid kind prefix, just normalize the name parts
    if parts[0] in _VALID_KINDS:
        return normalize_fqn(ref, parts[0], domain)

    # Heuristic: infer kind from naming conventions
    lower = ref.lower()
    if "lifecycle" in lower or "workflow" in lower or "approval" in lower:
        return normalize_fqn(ref, "workflow", domain)
    if "mixin" in lower or "stdlib" in parts[0] if parts else False:
        return normalize_fqn(ref, "mixin", domain)

    # Default: assume entity reference
    return normalize_fqn(ref, "entity", domain)

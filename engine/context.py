"""Contract-aware prompt builder — constructs system prompts with domain context.

Builds system prompts that give the LLM awareness of:
  - The Specora contract format and valid field types
  - The stdlib mixins and workflows available
  - Existing entities in the domain (for reference detection)

The prompts are crafted for each Factory interview phase so the LLM
knows exactly what to produce.

Usage:
    from engine.context import build_system_prompt

    prompt = build_system_prompt("entity_interview", domain="veterinary",
                                  existing_entities=["patient", "owner"])
"""

from __future__ import annotations

NAMING_CONVENTIONS = """
CRITICAL naming rules (contracts are rejected if violated):
  - Entity/workflow/page/route names: snake_case only.
    GOOD: task, todo_list, book_lifecycle
    BAD:  Task, TodoList, Book_Lifecycle
  - Fully Qualified Names (FQNs) in requires, references, state_machine:
    Format: kind/domain/name — ALL lowercase.
    GOOD: entity/todo_list/user, workflow/todo_list/task_lifecycle
    BAD:  todo_list/User, workflow/todo_list/Task_lifecycle
  - Graph edge names: SCREAMING_SNAKE_CASE only.
    GOOD: ASSIGNED_TO, REPORTED_BY, CONTAINS
    BAD:  assigned_to, AssignedTo, reportedBy
  - Domain names: snake_case only.
    GOOD: todo_list, veterinary, supply_chain
    BAD:  TodoList, Veterinary
""".strip()

FIELD_TYPES_REFERENCE = """
Valid field types:
  string   — Short text (names, codes, identifiers)
  integer  — Whole numbers (counts, years, IDs)
  number   — Decimal numbers (weights, prices, percentages)
  boolean  — True/false flags
  text     — Long text (descriptions, notes, content)
  array    — Lists (tags, items)
  object   — Nested structures
  datetime — Timestamps (ISO 8601)
  date     — Dates only (ISO 8601)
  uuid     — Unique identifiers
  email    — Email addresses

Special field features:
  required: true    — Must be provided on creation
  immutable: true   — Cannot change after creation
  enum: [a, b, c]   — Fixed set of allowed values
  computed: "now"    — Auto-set to current timestamp
  computed: "uuid"   — Auto-generated UUID
  references:        — Link to another entity
    entity: entity/domain/name    (FQN, all lowercase)
    display: field_name
    graph_edge: ASSIGNED_TO       (SCREAMING_SNAKE_CASE)
""".strip()

STDLIB_REFERENCE = """
Available standard library mixins (add via mixins list):
  mixin/stdlib/timestamped   — created_at, updated_at
  mixin/stdlib/identifiable  — id (UUID), number (sequential)
  mixin/stdlib/auditable     — created_at, updated_at, created_by, updated_by
  mixin/stdlib/taggable      — tags array
  mixin/stdlib/commentable   — comments array
  mixin/stdlib/soft_deletable — deleted_at, deleted_by, is_deleted

Available standard library workflows:
  workflow/stdlib/crud_lifecycle — active / archived
  workflow/stdlib/approval       — draft / submitted / approved / rejected
  workflow/stdlib/ticket         — new / assigned / in_progress / resolved / closed
""".strip()


def build_system_prompt(
    task: str,
    domain: str = "",
    existing_entities: list[str] | None = None,
) -> str:
    """Build a system prompt for a specific Factory task.

    Args:
        task: The interview type. One of:
            "domain_discovery" — initial domain exploration
            "entity_interview" — field discovery for one entity
            "workflow_interview" — state machine design
            "explain" — plain-English contract explanation
        domain: The domain being built.
        existing_entities: Entity names already defined in this domain.

    Returns:
        Complete system prompt string.
    """
    entities_ctx = ""
    if existing_entities:
        entity_list = ", ".join(existing_entities)
        entities_ctx = f"\nExisting entities in this domain: {entity_list}\n"

    prompts = {
        "domain_discovery": f"""You are a domain analyst helping a developer define their software domain.

Your job is to discover the core entities (data models) their system needs.

Ask about:
1. What the system does (one sentence)
2. What are the main things being tracked/managed
3. How those things relate to each other
4. Whether any have lifecycles (state machines)

When you have a clear picture, output a YAML list of entity names with brief descriptions.

Domain being built: {domain}
{entities_ctx}""",

        "entity_interview": f"""You are a data modeling expert helping define entity fields for the Specora contract system.

Given a description of an entity, determine:
1. What fields it needs (name, type, description, required, constraints)
2. Whether any fields reference other entities
3. Whether it needs a state machine
4. Which stdlib mixins to include

{NAMING_CONVENTIONS}

{FIELD_TYPES_REFERENCE}

{STDLIB_REFERENCE}

Domain: {domain}
{entities_ctx}

Output structured YAML for the entity's fields, references, and mixins.
Always include mixin/stdlib/timestamped and mixin/stdlib/identifiable unless explicitly unwanted.

IMPORTANT: All names must be snake_case. All FQNs must be kind/domain/name format, all lowercase.
All graph_edge values must be SCREAMING_SNAKE_CASE (e.g., ASSIGNED_TO, not assigned_to).""",

        "workflow_interview": f"""You are a workflow designer helping define state machines for entities.

Given a description of an entity's lifecycle, determine:
1. What states it has (with labels and categories: open/hold/closed)
2. What transitions are valid
3. What guards (required fields) exist for transitions
4. Which states are terminal (no outgoing transitions)

{NAMING_CONVENTIONS}

Output structured YAML with initial, states, transitions, and guards.
All state names and workflow names must be snake_case (e.g., in_progress, not InProgress).

Domain: {domain}""",

        "explain": """You are a technical documentation expert. Explain the given Specora contract in clear, plain English.

Cover:
- What the entity/workflow/page represents
- Its fields and their purposes
- Relationships to other entities
- State machine (if any)
- Which mixins are included

Be concise but thorough. Use bullet points.""",
    }

    return prompts.get(task, f"You are a helpful assistant working on the {domain} domain.")

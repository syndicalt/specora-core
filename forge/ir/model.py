"""Intermediate Representation (IR) models — the target-agnostic application model.

The IR is the FIREWALL between contracts and generators. Generators import
ONLY this module — they never see raw contracts, YAML, or the parser.

The compilation pipeline:
    Contracts (YAML) -> Parser -> Validator -> Graph -> IR -> Generators -> Code

The IR captures the semantic content of contracts in a normalized,
target-agnostic form. A TypeScript generator and a Rust generator
both consume the same IR — they just emit different code.

Models (from leaf to root):
    ReferenceIR      — A field's reference to another entity
    FieldIR          — A single field with type, constraints, reference
    StateMachineIR   — States, transitions, guards, side effects
    EntityIR         — Data model: fields, mixins, state machine, AI hooks
    PageIR           — UI spec: route, views, actions, generation tier
    EndpointIR       — Single API endpoint behavior
    RouteIR          — API spec: endpoints, global behaviors
    AgentIR          — AI behavior: trigger, I/O, constraints
    MixinIR          — Reusable field group
    InfraIR          — Infrastructure config
    DomainIR         — Complete domain: all of the above combined

Usage:
    from forge.ir.model import DomainIR, EntityIR, FieldIR

    # Generators receive a DomainIR and produce code
    def generate_typescript(ir: DomainIR) -> list[GeneratedFile]:
        for entity in ir.entities:
            for field in entity.fields:
                # ... emit TypeScript interface field
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Field-Level IR
# =============================================================================


class ReferenceIR(BaseModel):
    """A field's reference to another entity.

    References are the primary mechanism for expressing relationships
    between entities. A single reference annotation drives:
      - Frontend: search picker component, display name resolution
      - Database: foreign key constraint
      - Graph: Neo4j relationship edge

    Attributes:
        target_entity: FQN of the referenced entity (e.g., "entity/itsm/user").
        display_field: Which field on the target to display (e.g., "name").
        graph_edge: Neo4j relationship type (e.g., "ASSIGNED_TO").
        graph_direction: Edge direction — "forward" or "reverse".
    """

    target_entity: str
    display_field: str = "name"
    graph_edge: Optional[str] = None
    graph_direction: Optional[str] = None


class FieldIR(BaseModel):
    """A single field in an entity or mixin.

    Fields are the atomic building blocks of data models. Each field
    has a normalized type that maps to language-specific types in
    each generator target.

    Type mapping:
        IR type     | Python        | TypeScript    | PostgreSQL
        ----------- | ------------- | ------------- | ----------
        string      | str           | string        | TEXT
        integer     | int           | number        | INTEGER
        number      | float         | number        | NUMERIC
        boolean     | bool          | boolean       | BOOLEAN
        text        | str           | string        | TEXT
        array       | list          | Array<T>      | JSONB
        object      | dict          | Record<K,V>   | JSONB
        datetime    | datetime      | string (ISO)  | TIMESTAMPTZ
        date        | date          | string        | DATE
        uuid        | str           | string        | UUID
        email       | str           | string        | TEXT

    Attributes:
        name: Field name (snake_case).
        type: Normalized data type.
        description: Human-readable field description.
        required: Whether this field is required on creation.
        immutable: If true, cannot be changed after creation.
        default: Default value if not provided.
        format: Additional format hint (e.g., "uri", "hostname").
        enum_values: Allowed values (if the field is an enum).
        items_type: For array fields, the type of items.
        computed: Auto-computation expression (e.g., "now", "uuid", "sequence(...)").
        constraints: Validation constraints (min, max, maxLength, pattern, etc.).
        reference: Reference to another entity (if this is a FK field).
    """

    name: str
    type: str
    description: str = ""
    required: bool = False
    immutable: bool = False
    default: Any = None
    format: Optional[str] = None
    enum_values: Optional[list] = None
    items_type: Optional[str] = None
    computed: Optional[str] = None
    constraints: dict = Field(default_factory=dict)
    reference: Optional[ReferenceIR] = None


# =============================================================================
# Workflow IR
# =============================================================================


class StateIR(BaseModel):
    """A single state in a state machine.

    Attributes:
        name: State name (snake_case).
        label: Human-readable label (e.g., "In Progress").
        category: Grouping category — "open", "hold", or "closed".
        terminal: If true, no outgoing transitions.
        color: Optional color hint for UI.
    """

    name: str
    label: str = ""
    category: str = "open"
    terminal: bool = False
    color: Optional[str] = None


class GuardIR(BaseModel):
    """A transition guard — conditions for a state transition.

    Attributes:
        from_state: Source state.
        to_state: Target state.
        require_fields: Fields that must be non-null.
        condition: Free-text condition (for LLM/human implementation).
    """

    from_state: str
    to_state: str
    require_fields: list[str] = Field(default_factory=list)
    condition: Optional[str] = None


class StateMachineIR(BaseModel):
    """A complete state machine definition.

    State machines define the lifecycle of entities. They specify
    valid states, allowed transitions, guards (pre-conditions), and
    side effects (post-actions).

    Attributes:
        fqn: FQN of the source workflow contract.
        initial: The starting state for new records.
        states: All states in the machine.
        transitions: Map of source state -> list of valid target states.
        guards: Transition guards (conditions that must be met).
        side_effects: Actions triggered on transitions.
        type_overrides: Per-subtype transition overrides.
    """

    fqn: str = ""
    initial: str
    states: list[StateIR]
    transitions: dict[str, list[str]]
    guards: list[GuardIR] = Field(default_factory=list)
    side_effects: dict[str, list[dict]] = Field(default_factory=dict)
    type_overrides: dict[str, dict] = Field(default_factory=dict)


# =============================================================================
# Entity IR
# =============================================================================


class EntityIR(BaseModel):
    """A complete entity (data model) definition.

    Entities are the primary building blocks. They compile to database
    tables, TypeScript interfaces, Pydantic models, and Neo4j nodes.

    Attributes:
        fqn: Fully Qualified Name (e.g., "entity/itsm/incident").
        name: Entity name (e.g., "incident").
        domain: Domain namespace (e.g., "itsm").
        description: Human-readable description.
        table_name: PostgreSQL table name (inferred or explicit).
        fields: All fields (including expanded mixin fields).
        mixins_applied: FQNs of mixins that were expanded into this entity.
        state_machine: Bound state machine (from workflow contract).
        ai_hooks: AI integration hooks (event -> list of agent FQNs).
        number_prefix: Prefix for sequential IDs (e.g., "INC").
        icon: Lucide icon name.
    """

    fqn: str
    name: str
    domain: str
    description: str = ""
    table_name: str = ""
    fields: list[FieldIR] = Field(default_factory=list)
    mixins_applied: list[str] = Field(default_factory=list)
    state_machine: Optional[StateMachineIR] = None
    ai_hooks: dict[str, list[str]] = Field(default_factory=dict)
    number_prefix: Optional[str] = None
    icon: Optional[str] = None


# =============================================================================
# Page IR
# =============================================================================


class PageIR(BaseModel):
    """A UI page specification.

    Pages define what the frontend displays. Mechanical pages are
    generated from templates; creative pages require LLM generation.

    Attributes:
        fqn: FQN of the page contract.
        name: Page name.
        domain: Domain namespace.
        route: URL path (e.g., "/incidents").
        title: Page title.
        entity_fqn: FQN of the entity this page displays.
        generation_tier: "mechanical" or "creative".
        data_sources: API endpoints to fetch.
        display_rules: How to identify and link records.
        views: Available view modes (table, kanban, list, etc.).
        sections: Page sections for detail views.
        actions: User actions (create, bulk, etc.).
        filters: Filter configuration.
    """

    fqn: str
    name: str
    domain: str
    route: str
    title: str = ""
    entity_fqn: str = ""
    generation_tier: str = "mechanical"
    data_sources: list[dict] = Field(default_factory=list)
    display_rules: dict = Field(default_factory=dict)
    views: list[dict] = Field(default_factory=list)
    sections: list[dict] = Field(default_factory=list)
    actions: dict = Field(default_factory=dict)
    filters: dict = Field(default_factory=dict)


# =============================================================================
# Route IR
# =============================================================================


class EndpointIR(BaseModel):
    """A single API endpoint.

    Attributes:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE).
        path: URL path (e.g., "/", "/{id}", "/{id}/state").
        summary: Human-readable summary.
        request_fields: Fields accepted in the request body.
        required_fields: Fields required in the request body.
        validation_rules: Validation rules with on_fail responses.
        auto_fields: Auto-computed fields (field_name -> expression).
        side_effects: Actions triggered after the endpoint executes.
        response_status: HTTP status code for success.
        response_shape: Response body shape descriptor.
        hateoas_links: HATEOAS link definitions.
    """

    method: str
    path: str
    summary: str = ""
    request_fields: list[FieldIR] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    validation_rules: list[dict] = Field(default_factory=list)
    auto_fields: dict[str, str] = Field(default_factory=dict)
    side_effects: list[dict] = Field(default_factory=list)
    response_status: int = 200
    response_shape: dict = Field(default_factory=dict)
    hateoas_links: dict = Field(default_factory=dict)


class RouteIR(BaseModel):
    """An API route set for an entity.

    Attributes:
        fqn: FQN of the route contract.
        name: Route name.
        domain: Domain namespace.
        entity_fqn: FQN of the managed entity.
        base_path: Base URL path (e.g., "/incidents").
        endpoints: Individual endpoint definitions.
        global_behaviors: Behaviors inherited by all endpoints.
    """

    fqn: str
    name: str
    domain: str
    entity_fqn: str = ""
    base_path: str = ""
    endpoints: list[EndpointIR] = Field(default_factory=list)
    global_behaviors: dict = Field(default_factory=dict)


# =============================================================================
# Agent IR
# =============================================================================


class AgentIR(BaseModel):
    """An AI agent behavior definition.

    Attributes:
        fqn: FQN of the agent contract.
        name: Agent name.
        domain: Domain namespace.
        trigger: Event that activates this agent.
        threshold: Minimum confidence for auto-apply.
        input_entity: FQN of the entity the agent analyzes.
        input_fields: Specific fields the agent receives.
        output_updates: Map of field -> type/constraints the agent can set.
        approach: Algorithm/strategy description.
        constraints: Rules the agent must follow.
        fallback: Behavior when confidence is low.
    """

    fqn: str
    name: str
    domain: str
    trigger: str = ""
    threshold: float = 0.7
    input_entity: str = ""
    input_fields: list[str] = Field(default_factory=list)
    output_updates: dict = Field(default_factory=dict)
    approach: str = ""
    constraints: list[str] = Field(default_factory=list)
    fallback: dict = Field(default_factory=dict)


# =============================================================================
# Mixin IR
# =============================================================================


class MixinIR(BaseModel):
    """A reusable field group.

    Attributes:
        fqn: FQN of the mixin contract.
        name: Mixin name.
        domain: Domain namespace.
        description: Human-readable description.
        fields: The fields this mixin provides.
    """

    fqn: str
    name: str
    domain: str
    description: str = ""
    fields: list[FieldIR] = Field(default_factory=list)


# =============================================================================
# Infra IR
# =============================================================================


class InfraIR(BaseModel):
    """An infrastructure configuration.

    Attributes:
        fqn: FQN of the infra contract.
        name: Infra name.
        domain: Domain namespace.
        category: Infrastructure category (auth, deployment, etc.).
        config: Category-specific configuration.
        env_vars: Required environment variables.
        bootstrap: Data to seed on first creation.
    """

    fqn: str
    name: str
    domain: str
    category: str = ""
    config: dict = Field(default_factory=dict)
    env_vars: dict = Field(default_factory=dict)
    bootstrap: dict = Field(default_factory=dict)


# =============================================================================
# Domain IR — The Complete Application Model
# =============================================================================


class DomainIR(BaseModel):
    """The complete IR for a domain — everything a generator needs.

    This is the single object passed to generators. It contains the
    full compiled, validated, resolved, and expanded model of a domain.

    Generators iterate over the lists they care about:
      - TypeScript generator reads entities (for interfaces)
      - FastAPI generator reads routes + entities (for handlers)
      - PostgreSQL generator reads entities (for DDL)
      - OpenAPI generator reads routes (for spec)

    Attributes:
        domain: Domain namespace.
        entities: All entity definitions (with mixins expanded).
        workflows: Standalone workflow definitions.
        pages: All page specifications.
        routes: All route specifications.
        agents: All agent definitions.
        mixins: All mixin definitions (pre-expansion).
        infra: All infrastructure configurations.
    """

    domain: str
    entities: list[EntityIR] = Field(default_factory=list)
    workflows: list[StateMachineIR] = Field(default_factory=list)
    pages: list[PageIR] = Field(default_factory=list)
    routes: list[RouteIR] = Field(default_factory=list)
    agents: list[AgentIR] = Field(default_factory=list)
    mixins: list[MixinIR] = Field(default_factory=list)
    infra: list[InfraIR] = Field(default_factory=list)

    def summary(self) -> str:
        """Return a human-readable summary of the compiled IR."""
        parts = [f"Domain: {self.domain}"]
        if self.entities:
            parts.append(f"  Entities:  {len(self.entities)}")
            for e in self.entities:
                parts.append(f"    - {e.name} ({len(e.fields)} fields)")
        if self.workflows:
            parts.append(f"  Workflows: {len(self.workflows)}")
        if self.pages:
            parts.append(f"  Pages:     {len(self.pages)}")
            for p in self.pages:
                parts.append(f"    - {p.name} [{p.generation_tier}] -> {p.route}")
        if self.routes:
            parts.append(f"  Routes:    {len(self.routes)}")
            for r in self.routes:
                parts.append(f"    - {r.name} ({len(r.endpoints)} endpoints)")
        if self.agents:
            parts.append(f"  Agents:    {len(self.agents)}")
        if self.infra:
            parts.append(f"  Infra:     {len(self.infra)}")
        return "\n".join(parts)

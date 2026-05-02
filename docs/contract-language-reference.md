# Contract Language Reference

This is the complete reference for the Specora contract language. Every contract kind, every field, with full examples.

## Contract Envelope

Every Specora contract file uses the `.contract.yaml` extension and conforms to a common envelope structure:

```yaml
apiVersion: specora.dev/v1          # Always this value for v1 contracts
kind: Entity                        # One of: Entity, Workflow, Page, Route, Agent, Mixin, Infra
metadata:
  name: incident                    # snake_case, unique within kind+domain
  domain: itsm                      # Namespace grouping related contracts
  description: "A disruption..."    # Human-readable description
  tags: [ticketing, operational]    # Classification tags
  version: "1.0.0"                  # Optional semantic version
requires:                           # Explicit dependencies (FQN format)
  - entity/itsm/user
  - mixin/stdlib/timestamped
spec:                               # Kind-specific content (see below)
  ...
```

### Fully Qualified Name (FQN)

Every contract is identified by its FQN: `kind/domain/name`

- Kind is lowercased: `entity`, `workflow`, `page`, `route`, `agent`, `mixin`, `infra`
- Domain is the `metadata.domain` value
- Name is the `metadata.name` value

Examples: `entity/itsm/incident`, `mixin/stdlib/timestamped`, `workflow/library/book_lifecycle`

### The `requires` Array

Dependencies can be declared explicitly. The compiler also derives dependency graph edges from semantic references inside `spec`, such as field references, `mixins`, `state_machine`, route/page `entity`, agent input entity, and route side-effect FQNs.

The compiler uses the combined explicit and semantic dependencies to:
1. Build a dependency graph
2. Detect circular dependencies
3. Determine compilation order (topological sort)
4. Validate that all referenced contracts exist

Every FQN in `requires` and every semantic FQN reference must resolve to an existing contract. Keeping `requires` explicit is still useful for human readers and review, but Forge does not rely on authors duplicating every semantic reference manually.

---

## Entity Contracts

**Kind**: `Entity`  
**Purpose**: Define a data model with fields, references, mixins, and state machine binding.  
**Compiles to**: Database tables, TypeScript interfaces, Pydantic models, Neo4j nodes  

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `fields` | object | **Yes** | Map of field name to field definition |
| `mixins` | array[string] | No | FQNs of mixin contracts to compose |
| `state_machine` | string | No | FQN of workflow contract for lifecycle |
| `table` | string | No | Override PostgreSQL table name |
| `number_prefix` | string | No | Prefix for sequential IDs (e.g., "INC") |
| `icon` | string | No | Lucide icon name for UI |
| `ai_integration` | object | No | AI agent hooks by event |

### Field Definition

Each field in the `fields` map supports:

| Property | Type | Description |
|----------|------|-------------|
| `type` | string | **Required.** One of: string, integer, number, boolean, text, array, object, datetime, date, uuid, email |
| `description` | string | Human-readable description |
| `required` | boolean | Whether required on creation (default: false) |
| `immutable` | boolean | Cannot change after creation (default: false) |
| `default` | any | Default value if not provided |
| `format` | string | Additional format hint (uri, hostname, etc.) |
| `enum` | array | Allowed values |
| `items_type` | string | For array fields, the item type |
| `computed` | string | Auto-computation: "now", "now_on_update", "current_user", "uuid", "sequence(FMT)" |
| `constraints` | object | Validation: min, max, maxLength, minLength, pattern |
| `references` | object | Reference to another entity (see below) |

### Reference Annotation

```yaml
assigned_to:
  type: string
  references:
    entity: entity/itsm/user       # Target entity FQN
    display: name                    # Field to show instead of UUID
    graph_edge: ASSIGNED_TO          # Neo4j relationship type (UPPER_SNAKE)
    graph_direction: forward         # forward: this -> target, reverse: target -> this
```

A single reference annotation drives frontend (picker + display), database (FK), and graph (edge).

### Full Example

```yaml
apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: book
  domain: library
  description: "A book in the library catalog"
  tags: [catalog]
requires:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable
  - entity/library/author
  - workflow/library/book_lifecycle
spec:
  icon: book-open
  number_prefix: BOOK
  fields:
    title:
      type: string
      required: true
      constraints: { maxLength: 500 }
    author_id:
      type: string
      references:
        entity: entity/library/author
        display: name
        graph_edge: WRITTEN_BY
        graph_direction: forward
    genre:
      type: string
      enum: [fiction, non_fiction, science, history]
  mixins:
    - mixin/stdlib/timestamped
    - mixin/stdlib/identifiable
  state_machine: workflow/library/book_lifecycle
```

---

## Workflow Contracts

**Kind**: `Workflow`  
**Purpose**: Define a state machine with states, transitions, guards, and side effects.  
**Compiles to**: Backend validation logic, frontend state transition buttons, API constraint checking  

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `initial` | string | **Yes** | Starting state for new records |
| `states` | object | **Yes** | Map of state name to state definition |
| `transitions` | object | **Yes** | Map of source state to valid target states |
| `guards` | object | No | Conditions for specific transitions |
| `side_effects` | object | No | Actions triggered on transitions |
| `type_overrides` | object | No | Per-subtype transition overrides |

### State Definition

```yaml
states:
  in_progress:
    label: "In Progress"    # Human-readable label
    category: open           # open, hold, or closed (for UI grouping)
    terminal: false          # If true, no outgoing transitions
    color: blue              # Optional UI color hint
```

### Guards

Guards use the format `"source -> target"` as keys:

```yaml
guards:
  "in_progress -> resolved":
    require_fields: [resolution_notes]
  "assigned -> in_progress":
    condition: "assigned_to must not be null"
```

### Full Example

```yaml
apiVersion: specora.dev/v1
kind: Workflow
metadata:
  name: book_lifecycle
  domain: library
  description: "Book availability lifecycle"
requires: []
spec:
  initial: available
  states:
    available: { label: Available, category: open, color: green }
    checked_out: { label: Checked Out, category: open, color: blue }
    lost: { label: Lost, category: closed, color: red }
  transitions:
    available: [checked_out, lost]
    checked_out: [available, lost]
    lost: [available]
  guards:
    "available -> checked_out":
      require_fields: [checked_out_to]
```

---

## Page Contracts

**Kind**: `Page`  
**Purpose**: Define a UI page specification — what to display, how to display it.  

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `route` | string | **Yes** | URL path (starts with /) |
| `entity` | string | **Yes** | FQN of the entity this page displays |
| `generation_tier` | string | **Yes** | "mechanical" (template) or "creative" (LLM) |
| `title` | string | No | Page title |
| `data_sources` | array | No | API endpoints to fetch |
| `display_rules` | object | No | Record identification rules |
| `views` | array | No | Available view modes |
| `actions` | object | No | User actions (create, bulk) |
| `filters` | object | No | Filter configuration |

---

## Route Contracts

**Kind**: `Route`  
**Purpose**: Define API endpoint behavior — validation, auto-fields, side effects, HATEOAS.  

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entity` | string | **Yes** | FQN of the managed entity |
| `endpoints` | array | **Yes** | Endpoint definitions |
| `base_path` | string | No | Base URL path (inferred from entity if omitted) |
| `global_behaviors` | object | No | Rules inherited by all endpoints |

### Endpoint Definition

```yaml
endpoints:
  - method: POST
    path: /
    summary: "Create a new book"
    request_body:
      required_fields: [title]
      optional_fields: [isbn, author_id]
    validation:
      - rule: "title is required"
        on_fail: { status: 422, error: validation_error }
    auto_fields:
      id: uuid
      created_at: now
    side_effects:
      - emit_event: book_created
    response:
      status: 201
      shape: entity
    hateoas:
      self: /books/{id}
```

---

## Agent Contracts

**Kind**: `Agent`  
**Purpose**: Define AI behavior with guardrails — trigger, I/O, constraints, fallback.  

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `trigger` | string | **Yes** | Event that activates the agent |
| `input` | object | **Yes** | What the agent receives (entity FQN + fields) |
| `output` | object | **Yes** | What the agent can modify (updates map) |
| `threshold` | number | No | Minimum confidence for auto-apply (default: 0.7) |
| `approach` | string | No | Algorithm description |
| `constraints` | array | No | Rules the agent MUST follow |
| `fallback` | object | No | Behavior when confidence is low |

---

## Mixin Contracts

**Kind**: `Mixin`  
**Purpose**: Reusable field groups composable into entities.  

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `fields` | object | **Yes** | Field definitions (same schema as Entity fields) |

### Standard Library Mixins

| Mixin | Fields | Description |
|-------|--------|-------------|
| `mixin/stdlib/timestamped` | created_at, updated_at | Auto-managed timestamps |
| `mixin/stdlib/identifiable` | id, number | UUID primary key + sequential number |
| `mixin/stdlib/auditable` | created_at, updated_at, created_by, updated_by | Full audit trail |
| `mixin/stdlib/taggable` | tags | Free-form classification tags |
| `mixin/stdlib/commentable` | comments | Discussion thread |
| `mixin/stdlib/soft_deletable` | deleted_at, deleted_by, is_deleted | Soft delete support |

---

## Infra Contracts

**Kind**: `Infra`  
**Purpose**: Infrastructure configuration — auth, deployment, database, graph.  

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | string | **Yes** | One of: auth, deployment, database, graph, middleware, components, monitoring, search |
| `config` | object | No | Category-specific configuration |
| `env_vars` | object | No | Required environment variables |
| `bootstrap` | object | No | Data to seed on first creation |

# CLAUDE.md — Specora Core LLM Operating Manual

Specora Core is a **Contract-Driven Development engine**. You write YAML contracts, the engine generates a complete production application. Contracts are the source of truth — code is derived and disposable.

**Your job as an LLM**: read contracts, write contracts, validate, generate, and manage the self-healing loop. You do NOT need the CLI. Call Python functions directly.

---

## Python API — Direct Function Calls

### Validate Contracts

```python
from pathlib import Path
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all

contracts = load_all_contracts(Path("domains/my_domain"))
errors = validate_all(contracts)
# errors is a list of ContractValidationError(contract_fqn, path, message, severity)
# Empty list = all valid
```

### Compile to IR

```python
from forge.ir.compiler import Compiler

compiler = Compiler(contract_root=Path("domains/my_domain"))
ir = compiler.compile()  # Returns DomainIR
print(ir.summary())      # "Entities: 4, Workflows: 1, Routes: 3..."
```

### Generate Production Code

```python
from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator, DockerGenerator, TestSuiteGenerator
from forge.targets.postgres.gen_ddl import PostgresGenerator
from forge.targets.typescript.gen_types import TypeScriptGenerator

output = Path("runtime/")
for gen in [FastAPIProductionGenerator(), PostgresGenerator(), DockerGenerator(), TypeScriptGenerator(), TestSuiteGenerator()]:
    for f in gen.generate(ir):
        path = output / f.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f.content, encoding="utf-8")
```

### Emit a New Entity Contract

```python
from factory.emitters.entity_emitter import emit_entity

yaml_str = emit_entity("review", "helpdesk", {
    "description": "A customer review of a support interaction",
    "fields": {
        "rating": {"type": "integer", "required": True, "description": "1-5 star rating", "constraints": {"min": 1, "max": 5}},
        "comment": {"type": "text", "description": "Review text"},
        "customer_id": {"type": "uuid", "required": True, "references": {
            "entity": "entity/helpdesk/customer",
            "display": "name",
            "graph_edge": "REVIEWED_BY",
        }},
    },
    "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
})
# Write to domains/helpdesk/entities/review.contract.yaml
```

### Emit Other Contract Types

```python
from factory.emitters.route_emitter import emit_route
from factory.emitters.page_emitter import emit_page
from factory.emitters.workflow_emitter import emit_workflow

# Route (CRUD endpoints)
yaml_str = emit_route("reviews", "helpdesk", "entity/helpdesk/review")

# Page (UI spec)
yaml_str = emit_page("reviews", "helpdesk", "entity/helpdesk/review", ["rating", "comment", "customer_id"])

# Workflow (state machine)
yaml_str = emit_workflow("review_lifecycle", "helpdesk", {
    "initial": "pending",
    "states": {"pending": {"label": "Pending"}, "approved": {"label": "Approved"}, "rejected": {"label": "Rejected"}},
    "transitions": {"pending": ["approved", "rejected"]},
})
```

### Normalize a Contract

```python
from forge.normalize import normalize_contract
import yaml

contract = yaml.safe_load(Path("domains/x/entities/y.contract.yaml").read_text())
normalize_contract(contract)  # Fixes casing, FQNs, graph edges in-place
```

### Healer — Check Status

```python
from healer.queue import HealerQueue
queue = HealerQueue()
tickets = queue.list_tickets()
stats = queue.stats()  # {"by_status": {"queued": 3, "proposed": 1, ...}, "total": 4}
```

### Healer — Approve a Fix

```python
from healer.pipeline import HealerPipeline
from healer.queue import HealerQueue
queue = HealerQueue()
pipeline = HealerPipeline(queue=queue)
pipeline.approve_ticket("ticket-uuid-here")
```

### Extract Contracts from Existing Code

```python
from extractor.synthesizer import synthesize
report = synthesize(Path("/path/to/existing/codebase"), domain="my_app")
print(report.summary())  # "3 entities, 2 routes, 1 workflow"
```

---

## Contract Language Reference

### Contract Envelope (required on ALL contracts)

```yaml
apiVersion: specora.dev/v1
kind: Entity                    # Entity | Workflow | Page | Route | Agent | Mixin | Infra
metadata:
  name: snake_case_name         # MUST match ^[a-z][a-z0-9_]*$
  domain: snake_case_domain     # MUST match ^[a-z][a-z0-9_]*$
  description: "Human-readable"
  tags: [optional, tags]
requires:                       # Dependencies as FQNs
  - entity/domain/name          # MUST match ^(entity|workflow|page|route|agent|mixin|infra)/[a-z][a-z0-9_/]*$
spec:
  # Kind-specific content below
```

### Entity Contract

Defines a data model with fields, references, mixins, and optional state machine.

```yaml
apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: ticket
  domain: helpdesk
  description: "A support ticket"
requires:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable
  - entity/helpdesk/customer
  - entity/helpdesk/agent
  - workflow/helpdesk/ticket_lifecycle
spec:
  icon: ticket                   # Lucide icon name (optional)
  number_prefix: TKT             # 2-6 uppercase letters, MUST match ^[A-Z]{2,6}$
  fields:
    subject:
      type: string               # string|integer|number|boolean|text|array|object|datetime|date|uuid|email
      required: true
      description: "Ticket subject"
      constraints:
        maxLength: 300
    priority:
      type: string
      required: true
      enum: [critical, high, medium, low]
    customer_id:
      type: uuid
      required: true
      references:
        entity: entity/helpdesk/customer    # FQN of target entity
        display: name                        # Field to display instead of UUID
        graph_edge: SUBMITTED_BY             # MUST match ^[A-Z][A-Z0-9_]*$
    resolution:
      type: text
      description: "Resolution notes"
  mixins:
    - mixin/stdlib/timestamped
    - mixin/stdlib/identifiable
  state_machine: workflow/helpdesk/ticket_lifecycle
```

**Field types**: `string`, `integer`, `number`, `boolean`, `text`, `array`, `object`, `datetime`, `date`, `uuid`, `email`

**Field properties**: `type` (required), `required`, `description`, `enum`, `default`, `immutable`, `computed`, `constraints`, `references`, `format`, `items_type`

**DO NOT** invent field properties not in this list.

### Workflow Contract

Defines a state machine with states, transitions, guards, and side effects.

```yaml
apiVersion: specora.dev/v1
kind: Workflow
metadata:
  name: ticket_lifecycle
  domain: helpdesk
  description: "Ticket lifecycle"
requires: []
spec:
  initial: new
  states:
    new:
      label: New
      category: open            # open | hold | closed
    assigned:
      label: Assigned
      category: open
    resolved:
      label: Resolved
      category: closed
      terminal: true            # No outgoing transitions
  transitions:                  # Map of source_state -> [target_states]
    new: [assigned, closed]
    assigned: [in_progress, closed]
    in_progress: [resolved, closed]
  guards:                       # Key format: "source -> target"
    "new -> assigned":
      require_fields: [assigned_agent_id]
  side_effects:                 # Key format: "source -> target" or "* -> target"
    "* -> resolved":
      - set_field: {resolved_at: "now"}
```

### Route Contract

Defines API endpoints for an entity.

```yaml
apiVersion: specora.dev/v1
kind: Route
metadata:
  name: tickets
  domain: helpdesk
  description: "CRUD API for tickets"
requires:
  - entity/helpdesk/ticket
  - workflow/helpdesk/ticket_lifecycle
spec:
  entity: entity/helpdesk/ticket
  base_path: /tickets
  endpoints:
    - method: GET
      path: /
      summary: List tickets
      response: {status: 200, shape: list}
    - method: POST
      path: /
      summary: Create ticket
      auto_fields: {id: uuid, created_at: now}
      response: {status: 201, shape: entity}
    - method: GET
      path: /{id}
      summary: Get ticket
      response: {status: 200, shape: entity}
    - method: PATCH
      path: /{id}
      summary: Update ticket
      response: {status: 200, shape: entity}
    - method: DELETE
      path: /{id}
      summary: Delete ticket
      response: {status: 204}
    - method: PUT
      path: /{id}/state
      summary: Transition state
      request_body: {required_fields: [state]}
      response: {status: 200, shape: entity}
```

### Page Contract

Defines a UI page spec.

```yaml
apiVersion: specora.dev/v1
kind: Page
metadata:
  name: tickets
  domain: helpdesk
  description: "Browse tickets"
requires:
  - entity/helpdesk/ticket
spec:
  route: /tickets
  title: Support Tickets
  entity: entity/helpdesk/ticket
  generation_tier: mechanical
  data_sources:
    - endpoint: /tickets
      alias: tickets
  views:
    - type: table
      default: true
      columns: [subject, priority, customer_id, assigned_agent_id]
    - type: kanban
      card_fields: [subject, priority]
```

### Mixin Contract

Reusable field groups.

```yaml
apiVersion: specora.dev/v1
kind: Mixin
metadata:
  name: timestamped
  domain: stdlib
spec:
  fields:
    created_at:
      type: datetime
      computed: "now"
      immutable: true
    updated_at:
      type: datetime
      computed: "now_on_update"
```

### Infra Contract (Auth example)

```yaml
apiVersion: specora.dev/v1
kind: Infra
metadata:
  name: auth
  domain: helpdesk
spec:
  category: auth
  config:
    provider: jwt
    roles: [admin, agent, customer]
    protected_routes:
      - path: /tickets
        methods: [POST, PATCH, DELETE]
        roles: [admin, agent]
```

---

## Naming Rules (enforced by meta-schemas)

| Field | Pattern | Example |
|-------|---------|---------|
| `metadata.name` | `^[a-z][a-z0-9_]*$` | `ticket`, `task_lifecycle` |
| `metadata.domain` | `^[a-z][a-z0-9_]*$` | `helpdesk`, `stdlib` |
| `requires[]` entries | `^(entity\|workflow\|...)/[a-z][a-z0-9_/]*$` | `entity/helpdesk/ticket` |
| `references.graph_edge` | `^[A-Z][A-Z0-9_]*$` | `ASSIGNED_TO`, `SUBMITTED_BY` |
| `number_prefix` | `^[A-Z]{2,6}$` | `TKT`, `INC` |

---

## Standard Library

### Mixins (add to `requires` and `spec.mixins`)

| FQN | Fields added |
|-----|-------------|
| `mixin/stdlib/timestamped` | `created_at` (datetime), `updated_at` (datetime) |
| `mixin/stdlib/identifiable` | `id` (uuid), `number` (string, sequential) |
| `mixin/stdlib/auditable` | `created_at`, `updated_at`, `created_by`, `updated_by` |
| `mixin/stdlib/taggable` | `tags` (array) |
| `mixin/stdlib/commentable` | `comments` (array) |
| `mixin/stdlib/soft_deletable` | `deleted_at`, `deleted_by`, `is_deleted` |

### Workflows

| FQN | States |
|-----|--------|
| `workflow/stdlib/crud_lifecycle` | `active`, `archived` |
| `workflow/stdlib/approval` | `draft`, `submitted`, `approved`, `rejected` |
| `workflow/stdlib/ticket` | `new`, `assigned`, `in_progress`, `resolved`, `closed` |

---

## Project Structure

```
specora-core/
├── forge/                     # The compiler engine
│   ├── parser/                # loader.py, validator.py, graph.py
│   ├── ir/                    # model.py, compiler.py, passes/
│   ├── targets/               # Code generators
│   │   ├── fastapi_prod/      # Production FastAPI (repos, auth, Docker)
│   │   ├── fastapi/           # Simple FastAPI (in-memory, backward compat)
│   │   ├── postgres/          # PostgreSQL DDL
│   │   └── typescript/        # TypeScript interfaces
│   ├── normalize.py           # Contract normalization
│   ├── error_display.py       # Human-readable error formatting
│   └── diff/                  # Contract diff tracking
├── factory/                   # LLM-powered contract authoring
│   ├── emitters/              # entity, route, page, workflow emitters
│   ├── interviews/            # Domain, entity, workflow interviews
│   └── cli/                   # new, add, refine, explain, chat, visualize, migrate
├── healer/                    # Self-healing pipeline
│   ├── pipeline.py            # Orchestrator
│   ├── queue.py               # SQLite priority queue
│   ├── analyzer/              # Error classification + runtime tracing
│   ├── proposer/              # Deterministic + LLM fix proposals
│   ├── applier.py             # Apply fixes with rollback
│   ├── notifier.py            # Console + webhook + file notifications
│   └── api/server.py          # FastAPI HTTP service
├── extractor/                 # Reverse-engineer code → contracts
│   ├── scanner.py             # File discovery + classification
│   ├── analyzers/             # Python, TypeScript, route analyzers
│   ├── synthesizer.py         # 4-pass pipeline orchestrator
│   └── emitter.py             # AnalysisReport → contract YAML
├── engine/                    # LLM infrastructure
│   ├── engine.py              # LLMEngine (ask, chat)
│   ├── config.py              # Auto-detect provider from env vars
│   └── registry.py            # Model capabilities registry
├── spec/meta/                 # Meta-schemas (the law)
├── spec/stdlib/               # Standard library (mixins, workflows)
├── domains/                   # User contracts (the input)
└── runtime/                   # Generated code (the output, disposable)
```

---

## Generated App Architecture

The `fastapi-prod` generator produces:

```
runtime/
├── backend/
│   ├── app.py                 # FastAPI with CORS, error reporting to Healer
│   ├── config.py              # 12-factor env configuration
│   ├── models.py              # Pydantic Create/Update/Response models
│   ├── repositories/
│   │   ├── base.py            # Abstract repository per entity + factory functions
│   │   ├── memory.py          # In-memory adapter (dev/test)
│   │   └── postgres.py        # asyncpg adapter (production)
│   ├── auth/                  # Only if infra/auth contract exists
│   │   ├── interface.py       # AuthProvider ABC
│   │   ├── jwt_provider.py    # Built-in JWT
│   │   └── middleware.py      # require_auth, require_role dependencies
│   └── routes_*.py            # One per Route contract
├── database/schema.sql        # PostgreSQL DDL
├── Dockerfile                 # App container
├── Dockerfile.healer          # Healer sidecar container
├── docker-compose.yml         # App + Postgres + Healer (3 services)
├── requirements.txt
├── requirements.healer.txt
├── .env.example
└── types.ts                   # TypeScript interfaces
```

Routes call repository interfaces (not databases directly). Swap `DATABASE_BACKEND=postgres` to `memory` via env var. No code change needed.

---

## Healer HTTP API

The Healer runs as a Docker sidecar. The app auto-reports unhandled exceptions to it.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/healer/health` | GET | Health check |
| `/healer/status` | GET | Queue stats + success rates |
| `/healer/ingest` | POST | Submit error `{"source":"runtime","contract_fqn":"...","error":"...","context":{}}` |
| `/healer/tickets` | GET | List all tickets |
| `/healer/tickets/{id}` | GET | Ticket detail with proposal |
| `/healer/approve/{id}` | POST | Approve and apply a proposed fix |
| `/healer/reject/{id}` | POST | Reject with `{"reason":"..."}` |

---

## Environment Variables

### LLM Providers (at least one needed for Healer Tier 2-3, Factory, Chat)

| Variable | Provider | Notes |
|----------|----------|-------|
| `SPECORA_AI_MODEL` | Override | Force specific model (e.g., `claude-sonnet-4-6`, `glm-4.7-flash`) |
| `ANTHROPIC_API_KEY` | Anthropic | Recommended. Auto-selects `claude-sonnet-4-6` |
| `OPENAI_API_KEY` | OpenAI | Auto-selects `gpt-4o` |
| `XAI_API_KEY` | xAI | Auto-selects `grok-3-mini` at `api.x.ai/v1` |
| `ZAI_API_KEY` | Z.AI | Auto-selects `glm-4.7-flash` at `api.z.ai/api/paas/v4/`. Free tier available |
| `OLLAMA_BASE_URL` | Ollama | Local models. Auto-selects `llama3.3:70b` |

Priority: `SPECORA_AI_MODEL` > `ANTHROPIC` > `OPENAI` > `XAI` > `ZAI` > `OLLAMA`

### Generated App

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://specora:specora@localhost:5432/specora` | Postgres connection |
| `DATABASE_BACKEND` | `postgres` | `postgres` or `memory` |
| `PORT` | `8000` | API port |
| `CORS_ORIGINS` | `*` | Allowed origins |
| `AUTH_ENABLED` | `false` | Enable auth middleware |
| `AUTH_PROVIDER` | `jwt` | `jwt` or `external` |
| `AUTH_SECRET` | `change-me-in-production` | JWT signing secret |
| `AUTH_TOKEN_EXPIRE_MINUTES` | `60` | Token TTL |
| `SPECORA_HEALER_URL` | — | Healer endpoint for error reporting |
| `SPECORA_HEALER_PORT` | `8083` | Healer service port |
| `SPECORA_HEALER_WEBHOOK_URL` | — | Optional webhook for notifications |

---

## Common Workflows

### Create a new domain from scratch

```python
from pathlib import Path
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from factory.emitters.page_emitter import emit_page

domain = "shop"
base = Path("domains/shop")

# 1. Create entity
entity_yaml = emit_entity("product", domain, {
    "description": "A product for sale",
    "fields": {
        "name": {"type": "string", "required": True},
        "price": {"type": "number", "required": True},
        "category": {"type": "string", "enum": ["electronics", "clothing", "food"]},
    },
    "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
})
(base / "entities").mkdir(parents=True, exist_ok=True)
(base / "entities" / "product.contract.yaml").write_text(entity_yaml)

# 2. Create route
route_yaml = emit_route("products", domain, "entity/shop/product")
(base / "routes").mkdir(parents=True, exist_ok=True)
(base / "routes" / "products.contract.yaml").write_text(route_yaml)

# 3. Create page
page_yaml = emit_page("products", domain, "entity/shop/product", ["name", "price", "category"])
(base / "pages").mkdir(parents=True, exist_ok=True)
(base / "pages" / "products.contract.yaml").write_text(page_yaml)

# 4. Validate
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all
errors = validate_all(load_all_contracts(base))
assert not errors, f"Validation failed: {errors}"

# 5. Generate
from forge.ir.compiler import Compiler
from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator
from forge.targets.postgres.gen_ddl import PostgresGenerator
from forge.targets.fastapi_prod.gen_docker import generate_docker

ir = Compiler(contract_root=base).compile()
output = Path("runtime/")
for gen in [FastAPIProductionGenerator(), PostgresGenerator()]:
    for f in gen.generate(ir):
        p = output / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f.content)
for f in generate_docker(ir):
    p = output / f.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f.content)
```

### Fix validation errors

```python
from forge.normalize import normalize_contract
import yaml

path = Path("domains/shop/entities/product.contract.yaml")
contract = yaml.safe_load(path.read_text())
normalize_contract(contract)  # Fixes names, FQNs, graph edges
path.write_text(yaml.dump(contract, default_flow_style=False, sort_keys=False))
```

### Check healer and approve fixes

```python
from healer.queue import HealerQueue
from healer.pipeline import HealerPipeline

queue = HealerQueue()
pipeline = HealerPipeline(queue=queue)

# List proposed fixes
proposed = queue.list_tickets(status=TicketStatus.PROPOSED)
for t in proposed:
    print(f"{t.id[:8]}: {t.proposal.explanation}")

# Approve one
pipeline.approve_ticket(proposed[0].id)
```

### Deploy

```bash
docker compose up -d --build
```

---

## Build Rules

1. **Contracts are truth.** Never hand-edit generated code. Change the contract, regenerate.
2. **Every entity needs a route.** No entity is useful without API endpoints.
3. **Always include timestamped + identifiable mixins.** Every entity should have `id`, `created_at`, `updated_at`.
4. **FQNs are lowercase.** `entity/helpdesk/ticket`, never `entity/helpdesk/Ticket`.
5. **Graph edges are SCREAMING_SNAKE.** `ASSIGNED_TO`, never `assigned_to`.
6. **Validate before generating.** Always call `validate_all()` before `compile()`.
7. **The Healer runs as a sidecar.** The generated Docker stack includes it. It shares the app's `.env`.

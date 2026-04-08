# Getting Started with Specora Core

This guide walks you from zero to a running, self-healing application. The primary interface is your LLM coding agent (Claude Code, Cursor, Windsurf). The CLI exists for CI/CD and terminal users, but you will rarely need it.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Bootstrap a Project](#bootstrap-a-project)
4. [Your First Domain (LLM-Native)](#your-first-domain-llm-native)
5. [Generate and Deploy](#generate-and-deploy)
6. [Using the App](#using-the-app)
7. [Adding Features](#adding-features)
8. [The Self-Healing Loop](#the-self-healing-loop)
9. [Checking the Healer Queue](#checking-the-healer-queue)
10. [Extracting from Existing Code](#extracting-from-existing-code)
11. [Environment Variables](#environment-variables)
12. [Next Steps](#next-steps)

---

## Prerequisites

- **Python 3.10** or later
- **pip** (comes with Python)
- **Docker** and **Docker Compose** (for running the generated app)
- **An LLM coding agent** -- Claude Code, Cursor, or Windsurf (recommended)
- **(Optional)** An LLM API key for Healer Tier 2-3, Factory, and Chat features:
  - Anthropic (`ANTHROPIC_API_KEY`) -- recommended
  - OpenAI (`OPENAI_API_KEY`)
  - xAI (`XAI_API_KEY`)
  - Z.AI (`ZAI_API_KEY`) -- free tier available
  - Google (`GOOGLE_API_KEY`)
  - Ollama (`OLLAMA_BASE_URL`) -- local, no key needed

---

## Installation

```bash
pip install specora-core
```

To install with all LLM features (Factory, Healer Tier 2-3, Chat):

```bash
pip install "specora-core[all]"
```

Verify the install:

```bash
specora-init --help
```

Expected output:

```
Usage: specora-init [OPTIONS] NAME

  Scaffold a new standalone Specora project.

Options:
  -p, --path TEXT  Parent directory for the project
  --help           Show this message and exit.
```

---

## Bootstrap a Project

```bash
specora-init helpdesk
cd helpdesk
```

This creates the complete project structure:

```
helpdesk/
  domains/helpdesk/         <- Your contracts (source of truth)
    entities/
      example.contract.yaml <- Starter entity to replace
    workflows/
    routes/
    pages/
    agents/
  runtime/                  <- Generated code (disposable, gitignored)
  .forge/                   <- Healer state, diff tracking
  CLAUDE.md                 <- LLM operating manual
  .env                      <- Environment configuration
  .env.example              <- All variables documented
  .gitignore
  README.md
```

**What each piece does:**

| File/Directory | Purpose |
|---|---|
| `domains/helpdesk/` | Your contracts. This is the source of truth. Everything else is derived from these. |
| `runtime/` | Generated code. Disposable. Delete it and regenerate any time. |
| `.forge/` | Healer queue, diff history, internal state. |
| `CLAUDE.md` | The LLM reads this file automatically. It contains the full contract language reference, Python API, and build rules. |
| `.env` | Environment variables for the generated app and LLM providers. |

---

## Your First Domain (LLM-Native)

Open your LLM coding agent in the `helpdesk/` directory. The LLM reads `CLAUDE.md` and knows how to operate everything.

### The Conversation

```
You: "I want a helpdesk with agents, customers, and tickets.
      Agents are assigned to tickets. Customers submit tickets.
      Tickets have a lifecycle: new -> assigned -> in_progress -> resolved -> closed."

LLM: I'll create the contracts for your helpdesk domain. Let me start with
     the entities, workflow, routes, and pages.
```

The LLM writes contracts by calling Python functions directly:

```python
from pathlib import Path
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.workflow_emitter import emit_workflow
from factory.emitters.route_emitter import emit_route
from factory.emitters.page_emitter import emit_page

domain = "helpdesk"
base = Path("domains/helpdesk")

# 1. Create the customer entity
customer_yaml = emit_entity("customer", domain, {
    "description": "A helpdesk customer who submits tickets",
    "fields": {
        "name": {"type": "string", "required": True, "constraints": {"maxLength": 200}},
        "email": {"type": "email", "required": True},
        "company": {"type": "string"},
    },
    "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
})
(base / "entities" / "customer.contract.yaml").write_text(customer_yaml)

# 2. Create the agent entity
agent_yaml = emit_entity("agent", domain, {
    "description": "A support agent who handles tickets",
    "fields": {
        "name": {"type": "string", "required": True, "constraints": {"maxLength": 200}},
        "email": {"type": "email", "required": True},
        "department": {"type": "string", "enum": ["support", "billing", "engineering"]},
    },
    "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
})
(base / "entities" / "agent.contract.yaml").write_text(agent_yaml)

# 3. Create the ticket lifecycle workflow
workflow_yaml = emit_workflow("ticket_lifecycle", domain, {
    "initial": "new",
    "states": {
        "new": {"label": "New", "category": "open"},
        "assigned": {"label": "Assigned", "category": "open"},
        "in_progress": {"label": "In Progress", "category": "open"},
        "resolved": {"label": "Resolved", "category": "closed"},
        "closed": {"label": "Closed", "category": "closed", "terminal": True},
    },
    "transitions": {
        "new": ["assigned", "closed"],
        "assigned": ["in_progress", "closed"],
        "in_progress": ["resolved", "closed"],
    },
})
(base / "workflows" / "ticket_lifecycle.contract.yaml").write_text(workflow_yaml)

# 4. Create the ticket entity (references customer, agent, and workflow)
ticket_yaml = emit_entity("ticket", domain, {
    "description": "A support ticket submitted by a customer",
    "fields": {
        "subject": {"type": "string", "required": True, "constraints": {"maxLength": 300}},
        "description": {"type": "text"},
        "priority": {"type": "string", "required": True, "enum": ["critical", "high", "medium", "low"]},
        "customer_id": {
            "type": "uuid", "required": True,
            "references": {"entity": "entity/helpdesk/customer", "display": "name", "graph_edge": "SUBMITTED_BY"},
        },
        "assigned_agent_id": {
            "type": "uuid",
            "references": {"entity": "entity/helpdesk/agent", "display": "name", "graph_edge": "ASSIGNED_TO"},
        },
        "resolution": {"type": "text"},
    },
    "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
    "state_machine": "workflow/helpdesk/ticket_lifecycle",
    "number_prefix": "TKT",
})
(base / "entities" / "ticket.contract.yaml").write_text(ticket_yaml)

# 5. Create routes
for entity_name, route_name in [("customer", "customers"), ("agent", "agents"), ("ticket", "tickets")]:
    route_yaml = emit_route(route_name, domain, f"entity/helpdesk/{entity_name}")
    (base / "routes" / f"{route_name}.contract.yaml").write_text(route_yaml)

# 6. Create pages
for entity_name, page_name, columns in [
    ("customer", "customers", ["name", "email", "company"]),
    ("agent", "agents", ["name", "email", "department"]),
    ("ticket", "tickets", ["subject", "priority", "customer_id", "assigned_agent_id"]),
]:
    page_yaml = emit_page(page_name, domain, f"entity/helpdesk/{entity_name}", columns)
    (base / "pages" / f"{page_name}.contract.yaml").write_text(page_yaml)

# 7. Delete the starter entity
(base / "entities" / "example.contract.yaml").unlink(missing_ok=True)
```

Then the LLM validates:

```python
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all

contracts = load_all_contracts(base)
errors = validate_all(contracts)
print(f"{len(contracts)} contracts loaded, {len(errors)} errors")
# 10 contracts loaded, 0 errors
```

**CLI equivalent** (if you prefer terminal commands):

```bash
spc forge validate domains/helpdesk
# All 10 contracts are valid
```

---

## Generate and Deploy

The LLM compiles contracts to IR and generates all production code:

```python
from forge.ir.compiler import Compiler
from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator
from forge.targets.postgres.gen_ddl import PostgresGenerator
from forge.targets.fastapi_prod.gen_docker import generate_docker

# Compile
ir = Compiler(contract_root=Path("domains/helpdesk")).compile()
print(ir.summary())
# "Entities: 3, Workflows: 1, Routes: 3, Pages: 3"

# Generate
output = Path("runtime/")
for gen in [FastAPIProductionGenerator(), PostgresGenerator()]:
    for f in gen.generate(ir):
        p = output / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f.content, encoding="utf-8")

# Generate Docker files
for f in generate_docker(ir):
    p = output / f.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f.content, encoding="utf-8")

print("Generated files in runtime/")
```

**CLI equivalent:**

```bash
spc forge generate domains/helpdesk --target fastapi-prod
spc forge generate domains/helpdesk --target docker
```

### What Gets Generated

```
runtime/
  backend/
    app.py                   # FastAPI app with CORS, routers, health check
    config.py                # 12-factor environment configuration
    models.py                # Pydantic Create/Update/Response models
    routes_customers.py      # Customer CRUD endpoints
    routes_agents.py         # Agent CRUD endpoints
    routes_tickets.py        # Ticket CRUD + state transitions
    repositories/
      base.py                # Abstract interfaces + factory functions
      memory.py              # In-memory adapters (dev/test)
      postgres.py            # PostgreSQL adapters (production)
  database/
    schema.sql               # CREATE TABLE DDL with indexes
  Dockerfile                 # Python 3.12 slim container
  Dockerfile.healer          # Healer sidecar container
  docker-compose.yml         # App + Postgres + Healer (3 services)
  requirements.txt
  requirements.healer.txt
  .env.example
  types.ts                   # TypeScript interfaces
```

Every generated file has a `@generated` header. Never edit these files -- change the contract and regenerate.

### Boot It

```bash
cd runtime
docker compose up -d --build
```

This starts three services:
- **PostgreSQL 16** on port 5432 (schema auto-applied)
- **FastAPI app** on port 8000 (waits for Postgres health check)
- **Healer sidecar** on port 8083 (receives error reports from the app)

---

## Using the App

### Swagger UI

Open http://localhost:8000/docs in your browser. Full interactive API documentation is auto-generated.

### Health Check

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "domain": "helpdesk"}
```

### Create a Customer

```bash
curl -X POST http://localhost:8000/customers \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice Smith", "email": "alice@example.com", "company": "Acme Corp"}'
```

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Alice Smith",
  "email": "alice@example.com",
  "company": "Acme Corp",
  "created_at": "2026-04-07T12:00:00Z",
  "updated_at": "2026-04-07T12:00:00Z",
  "_links": {
    "self": "/customers/550e8400-e29b-41d4-a716-446655440000"
  }
}
```

### Create a Ticket

```bash
curl -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Cannot log in",
    "description": "Getting 403 error on login page",
    "priority": "high",
    "customer_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

### Transition Ticket State

```bash
curl -X PUT http://localhost:8000/tickets/{ticket_id}/state \
  -H "Content-Type: application/json" \
  -d '{"state": "assigned"}'
```

The state machine enforces valid transitions. Attempting `new -> resolved` returns a 400 error.

### List Tickets

```bash
curl http://localhost:8000/tickets
```

```json
{"items": [...], "total": 1}
```

---

## Adding Features

Back in your LLM session:

```
You: "Add a review entity. Customers can rate their ticket resolution
      with a 1-5 star rating and a comment."
```

The LLM writes the new contracts:

```python
# 1. Write the review entity contract
review_yaml = emit_entity("review", domain, {
    "description": "A customer review of a ticket resolution",
    "fields": {
        "rating": {
            "type": "integer", "required": True,
            "description": "1-5 star rating",
            "constraints": {"min": 1, "max": 5},
        },
        "comment": {"type": "text", "description": "Review text"},
        "ticket_id": {
            "type": "uuid", "required": True,
            "references": {"entity": "entity/helpdesk/ticket", "display": "subject", "graph_edge": "REVIEWS"},
        },
        "customer_id": {
            "type": "uuid", "required": True,
            "references": {"entity": "entity/helpdesk/customer", "display": "name", "graph_edge": "REVIEWED_BY"},
        },
    },
    "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
})
(base / "entities" / "review.contract.yaml").write_text(review_yaml)

# 2. Write the route
route_yaml = emit_route("reviews", domain, "entity/helpdesk/review")
(base / "routes" / "reviews.contract.yaml").write_text(route_yaml)

# 3. Write the page
page_yaml = emit_page("reviews", domain, "entity/helpdesk/review", ["rating", "comment", "ticket_id", "customer_id"])
(base / "pages" / "reviews.contract.yaml").write_text(page_yaml)

# 4. Validate
errors = validate_all(load_all_contracts(base))
assert not errors, f"Validation failed: {errors}"
print("0 errors -- all contracts valid")

# 5. Recompile and regenerate
ir = Compiler(contract_root=base).compile()
output = Path("runtime/")
for gen in [FastAPIProductionGenerator(), PostgresGenerator()]:
    for f in gen.generate(ir):
        p = output / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f.content, encoding="utf-8")
for f in generate_docker(ir):
    p = output / f.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f.content, encoding="utf-8")
```

Rebuild the running app:

```bash
cd runtime
docker compose up -d --build
```

The review endpoints are now live at `/reviews`. The database schema includes the new `reviews` table. No manual migration needed -- Docker re-applies the full schema on startup.

---

## The Self-Healing Loop

The generated app includes a Healer sidecar. When the app throws an unhandled exception, it auto-reports the error to the Healer. The flow:

```
[1. Error Occurs]
    Runtime exception in the generated app
        |
        v
[2. Auto-Report]
    Error middleware POSTs to Healer at http://healer:8083/healer/ingest
        |
        v
[3. Classification]
    Healer classifies the error: type, tier (1/2/3), priority
        |
        v
[4. Proposal]
    Tier 1: Deterministic fix (auto-applied, confidence 1.0)
    Tier 2-3: LLM proposes a contract fix (requires your approval)
        |
        v
[5. Approval]
    You review the proposal and approve or reject
        |
        v
[6. Contract Updated]
    The fix is applied to the .contract.yaml file
    Diff recorded in .forge/diffs/
        |
        v
[7. Regenerate + Rebuild]
    Forge recompiles and regenerates code
    docker compose up -d --build
        |
        v
[8. Bug Gone]
    The contract is now smarter. That class of bug cannot recur.
```

The key insight: bugs are fixed at the specification level, not in generated code. The contract gets smarter with every fix. Generated code is disposable.

---

## Checking the Healer Queue

### Python API (what the LLM uses)

```python
from healer.queue import HealerQueue
from healer.models import TicketStatus

queue = HealerQueue()

# Check overall stats
stats = queue.stats()
print(stats)
# {"by_status": {"queued": 0, "proposed": 2, "applied": 5}, "total": 7}

# List proposed fixes awaiting approval
proposed = queue.list_tickets(status=TicketStatus.PROPOSED)
for t in proposed:
    print(f"  {t.id[:8]}: [{t.priority}] {t.contract_fqn}")
    print(f"    Error: {t.raw_error[:80]}")
    print(f"    Fix: {t.proposal.explanation}")
    print()

# Approve a fix
from healer.pipeline import HealerPipeline
pipeline = HealerPipeline(queue=queue)
pipeline.approve_ticket(proposed[0].id)
print("Fix applied. Regenerate and rebuild.")
```

### CLI equivalent

```bash
# Check status
spc healer status

# List proposed fixes
spc healer tickets --status proposed

# Review a specific ticket
spc healer show a1b2c3d4

# Approve
spc healer approve a1b2c3d4

# Reject
spc healer reject a1b2c3d4 --reason "Wrong approach"
```

### HTTP API (for remote integration)

The Healer runs on port 8083 inside Docker. Full API reference in [healer.md](healer.md).

```bash
# Queue stats
curl http://localhost:8083/healer/status

# List tickets
curl http://localhost:8083/healer/tickets?status=proposed

# Approve a fix
curl -X POST http://localhost:8083/healer/approve/{ticket_id}
```

---

## Extracting from Existing Code

If you have an existing codebase, the Extractor reverse-engineers it into contracts:

### Python API

```python
from pathlib import Path
from extractor.synthesizer import synthesize

report = synthesize(Path("/path/to/existing/app"), domain="my_app")
print(report.summary())
# "3 entities, 2 routes, 1 workflow"
# "Scanned 47 files, analyzed 12 (0.3s)"
```

The Extractor runs a 4-pass pipeline:
1. **Scan** -- Discover and classify source files (Python, TypeScript)
2. **Extract** -- Parse models, routes, state patterns
3. **Cross-reference** -- Resolve relationships, detect workflows
4. **Synthesize** -- Build report, deduplicate, present for review

After extraction, you review each entity (accept or skip), then the Extractor writes `.contract.yaml` files.

### CLI equivalent

```bash
spc extract /path/to/existing/app --domain my_app
```

### Schema Migration (OpenAPI, SQL, Prisma)

```bash
spc factory migrate api-spec.yaml --domain my_app
spc factory migrate schema.sql --domain my_app --format sql
spc factory migrate schema.prisma --domain my_app
```

See [extractor.md](extractor.md) for the full pipeline documentation.

---

## Environment Variables

### LLM Providers (needed for Healer Tier 2-3, Factory, Chat)

| Variable | Provider | Notes |
|----------|----------|-------|
| `SPECORA_AI_MODEL` | Override | Force specific model (e.g., `claude-sonnet-4-6`) |
| `ANTHROPIC_API_KEY` | Anthropic | Recommended. Auto-selects `claude-sonnet-4-6` |
| `OPENAI_API_KEY` | OpenAI | Auto-selects `gpt-4o` |
| `XAI_API_KEY` | xAI | Auto-selects `grok-3-mini` |
| `ZAI_API_KEY` | Z.AI | Auto-selects `glm-4.7-flash`. Free tier available |
| `GOOGLE_API_KEY` | Google | Auto-selects `gemini-2.5-pro` |
| `OLLAMA_BASE_URL` | Ollama | Local models, no API key needed |

Priority order: `SPECORA_AI_MODEL` > `ANTHROPIC` > `OPENAI` > `XAI` > `ZAI` > `OLLAMA`

### Generated App

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://specora:specora@localhost:5432/specora` | Postgres connection string |
| `DATABASE_BACKEND` | `postgres` | `postgres` or `memory` |
| `PORT` | `8000` | API port |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `AUTH_ENABLED` | `false` | Enable JWT auth middleware |
| `AUTH_PROVIDER` | `jwt` | `jwt` or `external` |
| `AUTH_SECRET` | `change-me-in-production` | JWT signing secret |
| `AUTH_TOKEN_EXPIRE_MINUTES` | `60` | Token TTL |

### Healer

| Variable | Default | Purpose |
|----------|---------|---------|
| `SPECORA_HEALER_URL` | (none) | Healer endpoint for error reporting |
| `SPECORA_HEALER_PORT` | `8083` | Healer service port |
| `SPECORA_HEALER_WEBHOOK_URL` | (none) | Webhook for healer notifications |

---

## Next Steps

| I want to... | Read this |
|---|---|
| Understand the contract language | [CLAUDE.md](../CLAUDE.md) -- Contract Language Reference section |
| Understand the architecture | [architecture.md](architecture.md) |
| Deploy to production | [production-deployment.md](production-deployment.md) |
| Understand the Healer | [healer.md](healer.md) |
| Reverse-engineer my codebase | [extractor.md](extractor.md) |
| Set up an LLM provider | [llm-providers.md](llm-providers.md) |
| Use CLI commands | [cli-reference.md](cli-reference.md) |
| See every contract field option | [contract-language-reference.md](contract-language-reference.md) |

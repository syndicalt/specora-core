# Specora Core

**Software that fixes its own blueprints.**

[![Tests](https://img.shields.io/badge/tests-158%20passing-brightgreen)]() [![Python](https://img.shields.io/badge/python-3.10%2B-blue)]() [![License](https://img.shields.io/badge/license-Apache%202.0-orange)](LICENSE)

Specora Core is a Contract-Driven Development engine. You write YAML contracts describing your domain -- entities, workflows, routes, pages -- and the engine compiles them into a production application with a FastAPI backend, PostgreSQL database, Next.js frontend, and a self-healing sidecar that detects runtime failures and proposes contract fixes. If all your code is deleted but the contracts survive, you regenerate everything.

---

## Quick Start

```bash
pip install specora-core
specora-init my_app
cd my_app

# Open your LLM and start building, or:
spc forge generate domains/my_app \
  --target fastapi-prod \
  --target postgres \
  --target nextjs \
  --target docker

docker compose up -d
```

Your app is running. The Healer sidecar is watching for errors. Go break something.

---

## The Four Tiers

Specora Core is built as four cooperating systems:

### Forge -- The Compiler

Parses YAML contracts, validates them against meta-schemas, resolves dependencies, compiles to an intermediate representation, and generates production code. Seven contract kinds: Entity, Workflow, Route, Page, Agent, Mixin, Infra.

```
contracts (YAML) --> parse --> validate --> compile (IR) --> generate (code)
```

### Factory -- The Author

LLM-powered contract authoring. Describe what you want in natural language. Factory interviews you, emits valid contracts, and hands them to Forge. Supports entities, routes, pages, and workflows out of the box.

```bash
spc factory new entity --domain helpdesk
# "Describe the entity..." -> valid contract YAML
```

### Healer -- The Fixer

A sidecar service that receives runtime errors from your generated app, classifies them, traces them back to the contract that caused the bug, proposes a fix (deterministic for known patterns, LLM-assisted for novel ones), and applies it after approval. Software that debugs itself.

```
runtime error --> classify --> trace to contract --> propose fix --> approve --> regenerate
```

### Extractor -- The Reverse Engineer

Point it at an existing codebase. It scans Python files, TypeScript files, route definitions, and database schemas, then synthesizes contracts that describe what already exists. Migration path from legacy code to contract-driven development.

```bash
spc extractor synthesize /path/to/existing/app --domain my_app
```

---

## The Self-Healing Loop

This is the core idea. Contracts are not static documents -- they evolve:

```
 +------------------+
 |  YAML Contracts  |<-----------+
 +--------+---------+            |
          |                      |
     [Forge: compile]       [Healer: fix contract]
          |                      |
          v                      |
 +------------------+            |
 |  Generated Code  |           |
 +--------+---------+            |
          |                      |
     [Deploy & Run]         [Healer: classify & trace]
          |                      |
          v                      |
 +------------------+            |
 |  Runtime Error   +------------+
 +------------------+
```

1. You write contracts.
2. Forge generates a production app.
3. The app runs. Something breaks.
4. The Healer catches the error, traces it to the responsible contract, and proposes a fix.
5. You approve. The contract is patched. Forge regenerates. The app is redeployed.
6. The contract is now smarter than before.

Every bug makes the system better. Contracts accumulate institutional knowledge.

---

## What You Get (Generated Stack)

From a set of YAML contracts, Forge produces:

| Layer | Technology | Details |
|-------|-----------|---------|
| **API** | FastAPI | Repository pattern, CORS, auth middleware, error reporting to Healer |
| **Database** | PostgreSQL | DDL from entity schemas, migrations ready |
| **Frontend** | Next.js 15 | App Router, shadcn/ui, TypeScript types from contracts |
| **Healer** | FastAPI sidecar | Error ingestion, ticket queue, approve/reject UI |
| **Docker** | Compose | App + Postgres + Healer, one `docker compose up` |

The generated backend uses a repository interface -- swap between `postgres` and `memory` backends via environment variable, no code changes.

---

## The Demo

The `domains/helpdesk/` directory contains a complete helpdesk domain: tickets, customers, agents, SLA tracking, a ticket lifecycle workflow, CRUD routes, and UI pages. It serves as both documentation and proof:

```bash
# Validate the demo domain
spc forge validate domains/helpdesk

# Compile and generate
spc forge generate domains/helpdesk --target fastapi-prod --target postgres --target docker

# Run it
cd runtime && docker compose up -d

# Visit http://localhost:8000/docs for the API
# Visit http://localhost:8083/healer/status for the Healer dashboard
```

---

## How It Works

### Contracts In, Apps Out

```
domains/helpdesk/
  entities/
    ticket.contract.yaml       # Data model: fields, references, mixins
    customer.contract.yaml
  workflows/
    ticket_lifecycle.contract.yaml  # State machine: states, transitions, guards
  routes/
    tickets.contract.yaml      # API: endpoints, methods, response shapes
  pages/
    tickets.contract.yaml      # UI: views, columns, data sources
```

Each contract follows a strict envelope:

```yaml
apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: ticket
  domain: helpdesk
requires:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable
spec:
  # Kind-specific content
```

Seven kinds: **Entity**, **Workflow**, **Route**, **Page**, **Agent**, **Mixin**, **Infra**.

### Standard Library

Specora Core ships with reusable mixins and workflows:

- `mixin/stdlib/timestamped` -- `created_at`, `updated_at`
- `mixin/stdlib/identifiable` -- `id` (UUID), `number` (sequential)
- `mixin/stdlib/auditable` -- full audit trail fields
- `mixin/stdlib/taggable` -- tags array
- `mixin/stdlib/soft_deletable` -- soft delete support
- `workflow/stdlib/ticket` -- new, assigned, in_progress, resolved, closed
- `workflow/stdlib/approval` -- draft, submitted, approved, rejected

---

## Installation

### Minimal (Forge only -- no LLM, no Healer)

```bash
pip install specora-core
```

### Full (everything)

```bash
pip install "specora-core[all]"
```

### Development

```bash
git clone https://github.com/specora/specora-core.git
cd specora-core
pip install -e ".[all]"
pytest
```

---

## Project Structure

```
specora-core/
  forge/          # Compiler: parse, validate, compile, generate
  factory/        # LLM-powered contract authoring
  healer/         # Self-healing pipeline + sidecar API
  extractor/      # Reverse-engineer existing code to contracts
  engine/         # LLM infrastructure (multi-provider)
  spec/           # Meta-schemas and standard library
  domains/        # Example domains (helpdesk)
  runtime/        # Generated output (disposable)
  tests/          # 158 tests across the engine
  cli/            # CLI entry points
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [CLAUDE.md](CLAUDE.md) | Full architecture reference and LLM operating manual |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

---

## Environment Variables

### LLM Providers (needed for Factory, Healer Tier 2-3, Chat)

| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Anthropic (recommended) |
| `OPENAI_API_KEY` | OpenAI |
| `XAI_API_KEY` | xAI (Grok) |
| `ZAI_API_KEY` | Z.AI (free tier available) |
| `OLLAMA_BASE_URL` | Ollama (local models) |
| `SPECORA_AI_MODEL` | Force a specific model |

### Generated App

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://specora:specora@localhost:5432/specora` | Postgres connection |
| `DATABASE_BACKEND` | `postgres` | `postgres` or `memory` |
| `PORT` | `8000` | API server port |
| `SPECORA_HEALER_URL` | -- | Healer endpoint for error reporting |

See [CLAUDE.md](CLAUDE.md) for the full list.

---

## Philosophy

1. **Contracts are truth.** Code is derived and disposable. Delete `runtime/`, regenerate, nothing is lost.
2. **Every bug improves the system.** The Healer feedback loop means contracts get smarter over time.
3. **AI is a first-class citizen.** Factory authors contracts. Healer fixes them. Extractor reverse-engineers them. The engine orchestrates.
4. **No vendor lock-in.** Contracts are YAML. Generators are pluggable. Swap FastAPI for Django, Postgres for MySQL, Next.js for anything else.

---

## License

[Apache License 2.0](LICENSE) -- Copyright 2026 Nicholas Blanchard / Specora

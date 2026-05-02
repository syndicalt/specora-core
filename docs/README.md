# Specora Core Documentation

**Specora Core** is an LLM-native Contract-Driven Development engine. Write YAML contracts, generate a complete production application. Contracts are the source of truth -- code is a derived, disposable artifact.

**The workflow**: Install specora-core, scaffold a project with `specora-init`, open your LLM coding agent (Claude Code, Cursor, Windsurf) in the project directory, and talk to it. The LLM reads `CLAUDE.md` and operates everything via Python API. No CLI needed.

---

## I want to...

| Goal | Read this |
|------|-----------|
| Create a new project from scratch | [Getting Started](getting-started.md) |
| Understand the 5-tier architecture | [Architecture](architecture.md) |
| Understand contract memory and change tracking | [Architecture](architecture.md#contract-memory) |
| Write contracts (entity, workflow, route, page) | [CLAUDE.md](../CLAUDE.md) -- Contract Language Reference |
| See every contract field and option | [Contract Language Reference](contract-language-reference.md) |
| Deploy to production with Docker | [Production Deployment](production-deployment.md) |
| Understand the self-healing loop | [Self-Healing Loop](self-healing-loop.md) |
| Set up webhook notifications | [Webhooks](webhooks.md) |
| Understand database migrations | [Migrations](migrations.md) |
| Understand frontend generation | [Frontend Generation](frontend-generation.md) |
| Reverse-engineer an existing codebase | [Extractor](extractor.md) |
| Set up an LLM provider | [LLM Providers](llm-providers.md) |
| Use CLI commands (CI/CD, terminal) | [CLI Reference](cli-reference.md) |

---

## How It Works

```
1. pip install specora-core
2. specora-init my_app
3. cd my_app
4. Open your LLM (Claude Code, Cursor, Windsurf)
5. Talk to the LLM -- it reads CLAUDE.md and does everything
6. docker compose up -d  -- boots the generated app
```

The LLM writes contracts, validates them, compiles to IR, generates production code, and manages the self-healing loop. All via Python function calls, no CLI required.

---

## The Five Tiers

| Tier | Name | What It Does | LLM Required |
|------|------|-------------|--------------|
| 1 | **Forge** | Compile contracts, generate code. Deterministic. | No |
| 2 | **Factory** | LLM-powered contract authoring from natural language. | Yes |
| 3 | **Healer** | Self-healing: detect errors, propose contract fixes, apply. | Tier 1: No, Tier 2-3: Yes |
| 4 | **Extractor** | Reverse-engineer existing code into contracts. | No |
| 5 | **Advisor** | Proactive evolution from telemetry. | Planned |

---

## Documentation Index

### Getting Started

- **[Getting Started](getting-started.md)** -- Complete tutorial: install, bootstrap, build a domain, generate, deploy, heal. Follow this first.

### Reference

- **[Contract Language Reference](contract-language-reference.md)** -- Every contract kind (Entity, Workflow, Page, Route, Agent, Mixin, Infra), every field, every option.
- **[CLI Reference](cli-reference.md)** -- CLI commands for CI/CD and terminal users. The CLI is optional; the LLM calls Python functions directly.
- **[LLM Providers](llm-providers.md)** -- All 6 supported providers (Anthropic, OpenAI, xAI, Z.AI, Google, Ollama), auto-detection, model registry, configuration.

### Architecture

- **[Architecture](architecture.md)** -- The 5-tier model, compiler pipeline, IR layer, generator system, repository pattern, self-healing loop.
- **[Contract Memory Roadmap](contract-memory-roadmap.md)** -- Implemented contract-memory capabilities: semantic validation, provenance, guard execution, semantic dependencies, and change contracts.

### Production

- **[Production Deployment](production-deployment.md)** -- From contracts to a running API: `fastapi-prod` generator, Docker deployment, auth system, repository pattern, database backends.

### Self-Healing

- **[Self-Healing Loop](self-healing-loop.md)** -- The hero feature: complete flow from error to fix, tiered autonomy, error classification, LLM proposals, HTML approval page, auto-regeneration, live examples.
- **[Healer](healer.md)** -- The self-healing pipeline: intake, queue, classification, proposal, application, notification. Python API, HTTP API, tiered autonomy, webhook integration.
- **[Webhooks](webhooks.md)** -- Multi-channel notifications: Discord, Slack, Teams, raw JSON. Setup guides, message formats, HTML ticket view page.

### Code Generation

- **[Frontend Generation](frontend-generation.md)** -- Next.js 15 generator: DataTable, KanbanBoard, EntityForm, DetailView, AppSidebar, API client, Docker. How contracts drive every generated file.
- **[Migrations](migrations.md)** -- Versioned SQL migrations: IR cache, diffing, ALTER TABLE, destructive warnings, auto-run on Docker startup, the _migrations tracking table.

### Reverse Engineering

- **[Extractor](extractor.md)** -- Reverse-engineer existing codebases into contracts. 4-pass pipeline, supported languages, interactive review.

### LLM Operating Manual

- **[CLAUDE.md](../CLAUDE.md)** -- The file your LLM reads. Contains the full contract language reference, Python API, standard library, naming rules, and build rules.

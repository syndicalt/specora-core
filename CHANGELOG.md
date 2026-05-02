# Changelog

All notable changes to Specora Core will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-02

### Added

- Semantic IR validation before generation, covering cross-contract references, workflow state consistency, and guard field requirements.
- Machine-readable generated-file provenance via `Specora-Source`, with Healer tracing support.
- Workflow guard enforcement in generated memory and PostgreSQL repository adapters.
- Compiler-owned semantic dependency extraction for entities, routes, pages, agents, mixins, workflows, and route side effects.
- Change contracts attached to diffs, classifying compatibility, migration impact, affected surfaces, and verification expectations.

### Fixed

- Healer FastAPI endpoints now use async handlers, avoiding a Starlette/AnyIO sync-handler hang in API tests.

## [0.1.0] - 2026-04-08

### Added

- **Forge** -- Contract compiler engine: parser, validator, dependency graph, IR compiler, and code generators.
- **Factory** -- LLM-powered contract authoring with emitters for entities, routes, pages, and workflows. Interactive interviews for domain, entity, and workflow creation.
- **Healer** -- Self-healing pipeline: error classification, contract tracing, deterministic and LLM-assisted fix proposals, apply-with-rollback, SQLite priority queue, FastAPI sidecar API with approve/reject workflow.
- **Extractor** -- Reverse-engineer existing codebases into contracts: Python analyzer, TypeScript analyzer, route analyzer, 4-pass synthesis pipeline.
- **Production generators** -- FastAPI with repository pattern (postgres/memory backends), PostgreSQL DDL, TypeScript interfaces, Docker Compose (app + Postgres + Healer sidecar), test suite generation.
- **Next.js frontend generator** -- Page generation from Page contracts with table and kanban views.
- **Contract language** -- 7 contract kinds (Entity, Workflow, Route, Page, Agent, Mixin, Infra) with meta-schema validation and normalization.
- **Standard library** -- Reusable mixins (timestamped, identifiable, auditable, taggable, commentable, soft_deletable) and workflows (crud_lifecycle, approval, ticket).
- **Migration support** -- Extractor synthesizes contracts from legacy codebases for incremental adoption.
- **CLI** -- `spc` command with forge, factory, healer, and extractor subcommands. `specora-init` for project scaffolding.
- **REPL** -- Interactive contract development shell.
- **Multi-provider LLM engine** -- Anthropic, OpenAI, xAI, Z.AI, Ollama with automatic provider detection.
- **158 tests** across Forge, Factory, Healer, Extractor, and integration suites.
- **Drag-and-drop kanban** -- Generated frontend includes draggable cards between workflow state columns.
- **Auto-regeneration** -- Healer automatically regenerates frontend and backend after applying a fix.
- **Multi-channel webhooks** -- Comma-separated webhook URLs for notifications.

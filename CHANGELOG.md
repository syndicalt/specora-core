# Changelog

All notable changes to Specora Core will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Healer HTTP surface split into two authenticated planes.** It had none. The
  data plane (`/healer/ingest`, `/healer/status`) is internal-only and requires
  `SPECORA_HEALER_INGEST_TOKEN`; the public control plane (ticket view, approve,
  reject) requires an authenticated actor via pluggable credential schemes —
  signed single-use expiring approval tokens, a static operator bearer token, or
  an identity header from an authenticating proxy. Fails closed when
  unconfigured. `/healer/health` stays open for orchestrator probes.
- **Fixed stored XSS on the Healer ticket page.** `contract_fqn`, `context`, and
  `raw_error` arrive from the ingest endpoint and were interpolated into the
  approver's page with hand-rolled escaping that missed `&`, `"` and `'` — or
  with no escaping at all. Every interpolated value now goes through
  `html.escape`.
- **Added CSRF tokens** to the approve/reject forms, required whenever the
  authenticating credential could be attached ambiently by a browser.
- **Rate-limited ingest per `contract_fqn`**, so one failing contract cannot fan
  an error storm out into unbounded LLM spend.
- **The approving actor is now recorded** in the diff audit trail
  (`origin_detail`), not just the ticket id.

### Fixed

- **Healer applier no longer writes before validating.** It wrote the new
  contract, validated afterwards, and restored from a local variable on failure
  — so a crash between the two writes destroyed the contract with no recovery,
  and `write_text` truncating in place could leave partial YAML. It now
  validates, serialises, re-parses and re-validates the serialised bytes, then
  swaps the file in with `os.replace`. A missing file is reported rather than
  raised.
- **Healer no longer writes `_source_path` into user contracts.** Underscore-
  prefixed loader bookkeeping is stripped when a proposal is built and again
  before anything is written; an already-contaminated file is repaired and the
  repair is recorded on the ticket.
- **Healer ingest no longer blocks the event loop.** It returns 202 and a
  background worker drains the queue off-thread. This corrects the 0.2.0 entry
  below.
- **Healer queue is concurrency-safe.** Thread-local SQLite connections (one
  shared connection with `check_same_thread=False` was not thread-safe), WAL,
  and a `busy_timeout`. Claiming a ticket is a single atomic
  `UPDATE ... RETURNING`, closing a TOCTOU race where two workers claimed the
  same ticket and doubled LLM spend.

### Added

- **Healer LLM cost and latency accounting** per ticket, with a rolling-window
  token budget, an optional USD spend ceiling, and a consecutive-failure circuit
  breaker. Surfaced on `/healer/status`.
- **Proposal provenance** — model id, provider, prompt version, tokens, latency
  — stored with every proposal, and a confidence threshold that actually gates
  auto-apply.
- `spc healer link <ticket>` mints a signed approval link for operators without
  a webhook configured.

## [0.2.0] - 2026-05-02

### Added

- Semantic IR validation before generation, covering cross-contract references, workflow state consistency, and guard field requirements.
- Machine-readable generated-file provenance via `Specora-Source`, with Healer tracing support.
- Workflow guard enforcement in generated memory and PostgreSQL repository adapters.
- Compiler-owned semantic dependency extraction for entities, routes, pages, agents, mixins, workflows, and route side effects.
- Change contracts attached to diffs, classifying compatibility, migration impact, affected surfaces, and verification expectations.

### Fixed

- ~~Healer FastAPI endpoints now use async handlers, avoiding a Starlette/AnyIO sync-handler hang in API tests.~~

  **Corrected:** this was not a fix. Converting the handlers to `async def`
  removed the threadpool offloading Starlette gives synchronous handlers, so the
  synchronous `pipeline.process_next()` inside `ingest` — LLM round trips, file
  I/O, a full regeneration — began running directly on the event loop. Under an
  error storm every reported error serialised behind it, the health check timed
  out, and Docker restarted the Healer precisely when the app was failing. A
  test hang was traded for a production outage mode. Corrected in Unreleased:
  ingest returns 202 and a background worker drains the queue off the loop.

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

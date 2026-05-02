# Architecture

Specora Core is a five-tier, LLM-native Contract-Driven Development engine. It takes declarative YAML contracts as input and produces working software as output. The primary interface is your LLM coding agent (Claude Code, Cursor, Windsurf), which reads `CLAUDE.md` and operates the entire system via Python API calls.

This document describes the full architecture: the five tiers, the LLM-native workflow, the compiler pipeline, the Intermediate Representation, the generator system, the repository pattern, contract memory, the self-healing loop, and the project structure.

---

## The Five Tiers

```
Tier 1: FORGE — Deterministic generation
  Contract -> Compile -> IR -> Generate -> Code
  Zero tokens. Sub-second. Repeatable.

Tier 2: FACTORY — LLM-powered authoring
  Human describes feature -> LLM writes contract via Python API -> Forge generates
  One-time LLM cost. Conversational. Guided.

Tier 3: HEALER — Self-healing
  Error detected -> Healer classifies -> Proposes diff -> Auto-apply or human approval -> Forge regenerates
  Autonomous bug fixing at the specification level.

Tier 4: EXTRACTOR — Reverse-engineering
  Existing codebase -> Scan -> Extract -> Cross-reference -> Emit contracts
  Converts legacy code into the contract system.

Tier 5: ADVISOR — Proactive evolution (planned)
  Telemetry observed -> Advisor detects patterns -> Proposes new contracts -> Human approves
  The platform evolves based on how it's actually used.
```

### Tier 1: Forge (Compiler + Generators)

The core engine. Contracts go in, code comes out. The pipeline is entirely deterministic -- no LLM calls, no network requests, no randomness. Given the same contracts, it produces the same output every time.

Forge handles: validation, dependency resolution, IR compilation, and code generation for all targets (TypeScript, FastAPI, PostgreSQL, Docker, production FastAPI with repositories, and tests).

### Tier 2: Factory (LLM Authoring)

The Factory is a contract authoring system. Your LLM coding agent describes what you want and calls Python emitter functions (`emit_entity`, `emit_route`, `emit_page`, `emit_workflow`) to generate well-formed contracts. Those contracts then flow through Forge for code generation. The Factory can also be used via the CLI (`spc factory new`) for interactive interviews.

Requires at least one LLM provider configured (see [LLM Providers](llm-providers.md)).

### Tier 3: Healer (Self-Healing)

The Healer watches for errors -- validation failures, compilation errors, runtime exceptions -- and proposes fixes at the contract level. Tier 1 fixes (naming normalization, format corrections) are auto-applied. Tier 2-3 fixes (structural changes, missing fields) require human approval.

See [Healer Documentation](healer.md) for the full pipeline.

### Tier 4: Extractor (Reverse-Engineering)

The Extractor analyzes existing Python and TypeScript codebases to produce Specora contracts. It scans source files, extracts entities and routes, cross-references relationships, detects workflows, and emits `.contract.yaml` files.

See [Extractor Documentation](extractor.md) for details.

### Tier 5: Advisor (Planned)

The Advisor will monitor telemetry from running applications, detect usage patterns, and propose contract improvements. Not yet implemented.

---

## The LLM-Native Workflow

The primary interface for Specora Core is your LLM coding agent. The workflow:

```
1. pip install specora-core
2. specora-init my_app           # Scaffolds project with CLAUDE.md
3. cd my_app
4. Open LLM (Claude Code, Cursor, Windsurf)
5. Talk to the LLM               # It reads CLAUDE.md, calls Python API
6. docker compose up -d           # Boots the generated app
```

The LLM reads `CLAUDE.md` -- the LLM operating manual -- which contains:
- The full contract language reference (all 7 kinds)
- Python API for every operation (validate, compile, generate, emit, heal)
- Standard library (mixins, workflows)
- Naming rules and build rules

The LLM never needs the CLI. It calls Python functions directly:

```python
# Validate
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all
errors = validate_all(load_all_contracts(Path("domains/my_domain")))

# Compile
from forge.ir.compiler import Compiler
ir = Compiler(contract_root=Path("domains/my_domain")).compile()

# Generate
from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator
for f in FastAPIProductionGenerator().generate(ir):
    (Path("runtime") / f.path).parent.mkdir(parents=True, exist_ok=True)
    (Path("runtime") / f.path).write_text(f.content)
```

---

## The Generated Docker Stack

The `docker` generator produces a Docker Compose stack with three services:

```
docker-compose.yml
  |
  +-- db (PostgreSQL 16)
  |     Schema auto-applied via init script
  |     Health check: pg_isready
  |
  +-- app (FastAPI)
  |     Waits for db health check
  |     Error middleware reports to Healer
  |     Port 8000
  |
  +-- healer (Healer sidecar)
        Receives error reports from app
        Classifies, proposes fixes
        Port 8083
```

The app includes error reporting middleware that POSTs unhandled exceptions to the Healer sidecar at `http://healer:8083/healer/ingest`. The Healer classifies the error, proposes a contract-level fix, and queues it for approval. This closes the feedback loop between running software and contracts.

---

## The Compiler Pipeline

The Forge compiler transforms contracts into code through seven stages:

```
Contracts (.contract.yaml)
    |
    v
[1. Parser]        Load + discover .contract.yaml files, validate envelope
    |
    v
[2. Validator]     Check each contract against its kind-specific meta-schema
    |
    v
[3. Dep Graph]     Build dependency graph from requires + semantic refs
    |
    v
[4. IR Compiler]   Transform contracts into Intermediate Representation
    |
    v
[5. IR Passes]     Expand mixins, bind workflows, resolve references, infer tables
    |
    v
[6. Semantic Check] Validate IR coherence before generation
    |
    v
[7. Generators]    IR -> code (TypeScript, FastAPI, PostgreSQL, Docker, etc.)
```

### Stage 1: Parser (`forge/parser/loader.py`)

Discovers all `.contract.yaml` files recursively under a domain directory. Loads YAML, validates the envelope structure (`apiVersion`, `kind`, `metadata`, `spec`), and computes the Fully Qualified Name (FQN) for each contract. Also loads stdlib contracts from `spec/stdlib/`.

### Stage 2: Validator (`forge/parser/validator.py`)

Validates each contract against its kind-specific meta-schema using the `jsonschema` library. Meta-schemas are JSON Schema draft 2020-12 documents stored as YAML in `spec/meta/`. A local registry allows `$ref` between meta-schemas without network calls.

### Stage 3: Dependency Graph (`forge/parser/graph.py`)

Builds a directed graph where nodes are contracts and edges come from explicit `requires` arrays plus semantic references in contract specs. For example, entity field references, `mixins`, `state_machine`, route/page `entity`, agent input entity, and route side-effect FQNs all become graph edges. Performs:
- Cycle detection (DFS)
- Unresolved reference detection
- Topological sort for compilation order

### Stage 4: IR Compiler (`forge/ir/compiler.py`)

Transforms each contract into its IR representation, following topological order so dependencies are available when needed. Dispatches to kind-specific compilation methods (one per contract kind).

### Stage 5: IR Passes (`forge/ir/passes/`)

Post-compilation transformations that run in order:

1. **Mixin Expansion** (`mixin_expansion.py`) -- Copies mixin fields into entities. Entity fields take precedence on name conflicts.
2. **Table Name Inference** (`table_name_inference.py`) -- Infers PostgreSQL table names by pluralizing entity names.
3. **State Machine Binding** (`state_machine_binding.py`) -- Attaches workflow `StateMachineIR` to entities that reference them. Adds a `state` field with valid enum values.
4. **Reference Resolution** (`reference_resolution.py`) -- Validates that all cross-entity references point to existing entities. Infers route `base_path`s.

### Stage 6: Semantic Validation (`forge/ir/semantic.py`)

Validates the compiled IR before any generator runs. JSON Schema checks contract shape; semantic validation checks cross-contract meaning:

- entity, route, and page references resolve
- workflow initial states and transition targets are declared
- workflow guards match declared transitions
- guard `require_fields` exist on the bound entity
- reference display fields exist on the target entity

### Stage 7: Generators

Generators consume ONLY the `DomainIR`. They never import the parser, validator, or raw contracts. This is the IR firewall -- it ensures generators are target-agnostic and pluggable.

---

## The Intermediate Representation (IR)

The IR is the center of the architecture. It sits between contracts and generators as a normalized, target-agnostic application model.

```
                                  +-------------------+
                                  |   TypeScript      |
                    +------------>|   Generator       |--> types.ts
                    |             +-------------------+
+-----------+   +---+---+        +-------------------+
| Contracts |-->|  IR   |------->|   FastAPI          |--> routes.py, models.py
|  (YAML)   |   |       |        |   Generator       |
+-----------+   +---+---+        +-------------------+
                    |             +-------------------+
                    +------------>|   PostgreSQL      |--> schema.sql
                    |             |   Generator       |
                    |             +-------------------+
                    |             +-------------------+
                    +------------>| FastAPI-Prod      |--> repos, auth, docker
                                  |   Generator       |
                                  +-------------------+
```

### IR Models (`forge/ir/model.py`)

All models are Pydantic `BaseModel` subclasses:

| Model | Purpose |
|-------|---------|
| `DomainIR` | The complete domain -- everything a generator needs |
| `EntityIR` | Data model with expanded mixin fields, bound state machine |
| `FieldIR` | Normalized field: type, constraints, reference, computed |
| `ReferenceIR` | Cross-entity reference (FK + graph edge) |
| `StateMachineIR` | States, transitions, guards, side effects |
| `StateIR` | Single state with label, category, terminal flag |
| `PageIR` | UI specification: route, views, actions, filters |
| `RouteIR` | API route set: base path, endpoints, global behaviors |
| `EndpointIR` | Single API endpoint: method, path, validation, auto-fields |
| `AgentIR` | AI behavior: trigger, threshold, constraints, fallback |
| `MixinIR` | Reusable field group (pre-expansion) |
| `InfraIR` | Infrastructure config: auth, deployment, database |

### The IR Firewall

**Critical rule: Generators ONLY import `forge.ir.model`. They never see raw contracts, YAML, or the parser.**

This means:
- Any generator target can be added without touching the compiler
- The IR is the contract between compilation and generation
- Testing generators requires only constructing IR objects, not parsing YAML

---

## Generator System

### BaseGenerator Interface

Every generator implements `BaseGenerator` from `forge/targets/base.py`:

```python
class BaseGenerator(ABC):
    @abstractmethod
    def name(self) -> str:
        """Short lowercase name (e.g., 'typescript', 'fastapi-prod')."""
        ...

    @abstractmethod
    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        """Generate code files from the IR."""
        ...
```

Each `GeneratedFile` has a `path` (relative output path), `content` (full file text), and `provenance` (source contract FQN).

### Provenance Headers

Every generated file includes a provenance header:

```python
# ======================================================================
# @generated -- DO NOT EDIT
#
# This file was generated by Specora Forge from contract specifications.
# Any manual changes will be overwritten on the next generation.
#
# Specora-Source: entity/library/book
# Source: entity/library/book
# Generated: 2026-04-07 12:00 UTC
# ======================================================================
```

`Specora-Source` is the machine-readable line used by Healer to trace runtime stack frames back to the source contract. `Source` remains as human-readable context.

---

## Contract Memory

Contracts are the durable memory of the system. Specora preserves and enforces that memory in five ways:

| Capability | Module | Purpose |
|------------|--------|---------|
| Semantic validation | `forge/ir/semantic.py` | Reject incoherent contracts before generation |
| Semantic dependencies | `forge/parser/dependencies.py` | Compile in the right order even when dependencies are implied by spec fields |
| Generated provenance | `forge/provenance.py` | Trace generated files and runtime errors back to source contracts |
| Executable workflow guards | `forge/targets/fastapi_prod/` | Enforce workflow preconditions in generated runtime adapters |
| Change contracts | `forge/diff/change_contract.py` | Classify contract diffs by compatibility, migration impact, affected surfaces, and verification |

### Available Generators

| Target | Name | What it generates |
|--------|------|-------------------|
| TypeScript | `typescript` | Interfaces with typed fields and JSDoc |
| FastAPI (basic) | `fastapi` | Route handlers with inline Pydantic models |
| PostgreSQL | `postgres` | `CREATE TABLE` DDL with indexes |
| FastAPI (production) | `fastapi-prod` | Config, models, repositories, routes, app, auth |
| Docker | `docker` | Dockerfile, docker-compose.yml, .env.example, requirements.txt |
| Tests | `tests` | Black-box pytest tests from route contracts |
| Next.js | `nextjs` | Frontend pages, components, API client |
| Migrations | `migrations` | Incremental SQL migration files |

**Aliases:**

| Alias | Expands to |
|-------|-----------|
| `prod` (default) | `fastapi-prod` + `postgres` + `docker` + `tests` + `nextjs` |

### Adding a New Generator

1. Create a directory under `forge/targets/{name}/`
2. Implement `BaseGenerator` -- only import `forge.ir.model`
3. Register in `_get_generators()` in `forge/cli/main.py`

---

## Repository Pattern (Production Generator)

The `fastapi-prod` generator produces a clean layered architecture using the repository pattern:

```
backend/
  config.py              # 12-factor env config
  models.py              # Pydantic Create/Update/Response models
  app.py                 # FastAPI application with CORS, routers, health check
  routes_{entity}.py     # Route handlers -- call repository, not SQL
  auth/
    interface.py         # Abstract AuthProvider
    jwt_provider.py      # JWT implementation (bcrypt + python-jose)
    middleware.py         # require_auth, require_role dependencies
  repositories/
    base.py              # Abstract interfaces + factory functions
    memory.py            # In-memory dict adapters (dev/test)
    postgres.py          # PostgreSQL adapters (asyncpg)
```

### Abstract Repository Interface

For each entity, the generator creates an abstract base class:

```python
class BookRepository(ABC):
    async def list(self, limit, offset, filters) -> tuple[list[dict], int]: ...
    async def get(self, id: str) -> dict | None: ...
    async def create(self, data: dict) -> dict: ...
    async def update(self, id: str, data: dict) -> dict | None: ...
    async def delete(self, id: str) -> bool: ...
    async def transition(self, id: str, new_state: str) -> dict | None: ...  # if state machine
```

### Swapping Backends

The `get_{entity}_repo()` factory reads `DATABASE_BACKEND` from config:

```python
def get_book_repo() -> BookRepository:
    from backend.config import DATABASE_BACKEND
    if DATABASE_BACKEND == "postgres":
        from backend.repositories.postgres import PostgresBookRepository
        return PostgresBookRepository()
    from backend.repositories.memory import MemoryBookRepository
    return MemoryBookRepository()
```

Set `DATABASE_BACKEND=memory` for development/testing (no persistence).
Set `DATABASE_BACKEND=postgres` for production (asyncpg connection pool).

---

## The Self-Healing Loop

The self-healing loop is the end-to-end story that makes Specora Core more than a code generator. It closes the feedback loop between running software and contracts:

```
[1. Error Occurs]
    Runtime exception, validation failure, test failure, compilation error
        |
        v
[2. Healer Intake]
    Error is ingested via CLI (spc healer fix), HTTP API, or manual submission
        |
        v
[3. Classification]
    Classifier assigns: error_type, tier (1/2/3), priority (critical/high/medium/low)
        |
        v
[4. Proposal]
    Tier 1: Deterministic fix (normalize_contract)
    Tier 2-3: LLM-powered structural fix (reads contract + diff history)
        |
        v
[5. Approval]
    Tier 1: Auto-applied (confidence = 1.0)
    Tier 2-3: Queued for human approval
        |
        v
[6. Application]
    Fix is applied to the contract YAML file
    Diff is recorded in .forge/diffs/
        |
        v
[7. Regeneration]
    Forge recompiles and regenerates code from the updated contract
        |
        v
[8. Notification]
    Console log, JSONL file, optional webhook POST
```

This means bugs get fixed at the specification level, not in generated code. The contract gets smarter with every fix, and the diff history teaches the Healer what patterns of fixes work.

---

## Contract Diff Tracking

Every contract mutation is tracked by the diff system (`forge/diff/`):

```
Contract Change
    |
    v
+---------------+
|  Tracker      |  compute_diff(before, after)
|  (deepdiff)   |  -> list[FieldChange]
+-------+-------+
        |
        v
+---------------+
|  DiffStore    |  save(ContractDiff)
|  (.forge/)    |  -> JSON file + index
+-------+-------+
        |
        v
+----------------------+
|  LLM Context         |  format_for_llm(fqn, n=10)
|  (for Healer/Advisor)|  -> structured text
+----------------------+
```

Each diff records:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique diff identifier |
| `fqn` | string | Contract FQN |
| `timestamp` | datetime | When the change occurred |
| `origin` | enum | `human`, `healer`, `advisor`, `factory` |
| `origin_detail` | string | Additional context (ticket ID, user, etc.) |
| `reason` | string | Why the change was made |
| `changes` | list[FieldChange] | JSONPath-level changes |
| `before_hash` / `after_hash` | string | Content hashes |
| `before_snapshot` / `after_snapshot` | dict | Full contract before/after |
| `change_contract` | ChangeContract | Compatibility, migration impact, affected surfaces, verification expectations |

Change types: `added`, `removed`, `modified`, `type_changed`.

Storage: `.forge/diffs/` directory, file-based JSON indexed by FQN.

---

## Project Structure

```
specora-core/
|
+-- spec/                           # CONTRACT LANGUAGE DEFINITION
|   +-- meta/                       # Meta-schemas (one per contract kind)
|   |   +-- envelope.meta.yaml      # Common structure: apiVersion, kind, metadata
|   |   +-- entity.meta.yaml        # Entity contract validation
|   |   +-- workflow.meta.yaml      # Workflow (state machine) validation
|   |   +-- page.meta.yaml          # Page (UI spec) validation
|   |   +-- route.meta.yaml         # Route (API behavior) validation
|   |   +-- agent.meta.yaml         # Agent (AI behavior) validation
|   |   +-- mixin.meta.yaml         # Mixin (reusable fields) validation
|   |   +-- infra.meta.yaml         # Infrastructure validation
|   +-- stdlib/                     # Standard library contracts
|       +-- mixins/                 # timestamped, identifiable, auditable, taggable
|       +-- workflows/              # crud_lifecycle, approval, ticket
|
+-- forge/                          # TIER 1: THE COMPILER ENGINE
|   +-- parser/                     # Load, validate, resolve contracts
|   |   +-- loader.py               # Discover + load .contract.yaml files
|   |   +-- validator.py            # Validate against meta-schemas
|   |   +-- graph.py                # Dependency graph, cycle detection, topo sort
|   +-- ir/                         # Intermediate Representation
|   |   +-- model.py                # IR data models (DomainIR, EntityIR, etc.)
|   |   +-- compiler.py             # Contract -> IR transformation
|   |   +-- passes/                 # Post-compilation passes
|   |       +-- mixin_expansion.py
|   |       +-- table_name_inference.py
|   |       +-- state_machine_binding.py
|   |       +-- reference_resolution.py
|   +-- targets/                    # Code generators (IR -> code)
|   |   +-- base.py                 # BaseGenerator interface + provenance headers
|   |   +-- typescript/             # TypeScript interfaces
|   |   +-- fastapi/                # Basic FastAPI routes
|   |   +-- postgres/               # PostgreSQL DDL
|   |   +-- fastapi_prod/           # Production FastAPI (repos, auth, docker, tests)
|   |       +-- generator.py        # Orchestrator (3 generators: fastapi-prod, docker, tests)
|   |       +-- gen_config.py       # 12-factor config module
|   |       +-- gen_models.py       # Pydantic Create/Update/Response models
|   |       +-- gen_repositories.py # Abstract + Memory + Postgres adapters
|   |       +-- gen_routes.py       # Route handlers calling repositories
|   |       +-- gen_app.py          # FastAPI app with middleware stack
|   |       +-- gen_auth.py         # Auth interface + JWT provider + middleware
|   |       +-- gen_docker.py       # Dockerfile, compose, .env.example, requirements.txt
|   |       +-- gen_tests.py        # Black-box pytest tests (stub)
|   +-- diff/                       # Contract diff tracking
|   |   +-- models.py               # ContractDiff, FieldChange, DiffOrigin
|   |   +-- tracker.py              # Compute structural diffs
|   |   +-- store.py                # Persist + query diffs
|   +-- cli/                        # CLI commands
|       +-- main.py                 # Click-based CLI entry point
|
+-- factory/                        # TIER 2: LLM-POWERED AUTHORING
|
+-- healer/                         # TIER 3: SELF-HEALING PIPELINE
|   +-- models.py                   # HealerTicket, HealerProposal, enums
|   +-- queue.py                    # SQLite-backed priority queue
|   +-- pipeline.py                 # Pipeline orchestrator
|   +-- notifier.py                 # Console, webhook, file notifications
|   +-- monitor.py                  # Metrics and success rate tracking
|   +-- watcher.py                  # File system watcher
|   +-- applier.py                  # Apply proposals to contract files
|   +-- analyzer/                   # Error classification
|   |   +-- classifier.py           # Tier assignment, error typing
|   +-- proposer/                   # Fix proposal strategies
|   |   +-- deterministic.py        # Tier 1: normalize_contract()
|   |   +-- llm_proposer.py         # Tier 2-3: LLM-powered structural fixes
|   +-- api/                        # HTTP service
|   |   +-- server.py               # FastAPI endpoints for remote healing
|   +-- cli/                        # CLI commands
|       +-- commands.py             # fix, status, tickets, show, approve, reject, serve, history
|
+-- extractor/                      # TIER 4: REVERSE-ENGINEERING
|   +-- models.py                   # ExtractedEntity, ExtractedRoute, AnalysisReport
|   +-- scanner.py                  # Pass 1: File discovery and classification
|   +-- analyzers/                  # Pass 2: Language-specific extraction
|   |   +-- python_models.py        # Pydantic, SQLAlchemy, dataclass extraction
|   |   +-- typescript_types.py     # TypeScript interface/type extraction
|   |   +-- routes.py               # FastAPI/Express route extraction
|   +-- cross_ref.py                # Pass 3: Relationship resolution, workflow detection
|   +-- synthesizer.py              # Pass 4: Build AnalysisReport
|   +-- reporter.py                 # Interactive accept/skip per entity
|   +-- emitter.py                  # Write .contract.yaml files
|   +-- cli/                        # CLI commands
|       +-- commands.py             # spc extract
|
+-- engine/                         # SHARED LLM INFRASTRUCTURE
|   +-- config.py                   # Provider auto-detection from environment
|   +-- registry.py                 # Model capabilities catalog (15 models)
|
+-- advisor/                        # TIER 5: PROACTIVE EVOLUTION (planned)
|
+-- domains/                        # USER'S DOMAIN CONTRACTS (the input)
|   +-- library/                    # Example: Library domain
|       +-- entities/               # book, author, patron
|       +-- workflows/              # book_lifecycle
|       +-- pages/                  # books
|       +-- routes/                 # books API
|
+-- runtime/                        # GENERATED CODE (the output)
|   +-- backend/                    # Generated FastAPI routes + models
|   +-- frontend/                   # Generated frontend configs
|   +-- database/                   # Generated SQL DDL
|
+-- tests/                          # Test suite
+-- docs/                           # Documentation
+-- pyproject.toml                  # Package config
+-- .env.example                    # All environment variables
```

---

## Design Principles

1. **Contracts are the source of truth.** Code is a derived, disposable artifact. Delete all generated code, keep the contracts, and the engine regenerates everything.

2. **IR is the firewall.** Generators see only `forge.ir.model`. They never touch raw YAML or the parser. This makes targets pluggable and testable in isolation.

3. **Diffs, not replacements.** Every contract mutation is tracked with who/what/why context. This feeds the Healer and Advisor with historical context.

4. **Progressive complexity.** The stdlib provides simple building blocks. Domains compose them into complex models. The engine handles the mechanical parts; LLMs handle the creative parts.

5. **Meta-schemas enforce correctness.** Invalid contracts are caught at compile time, not at runtime. The meta-schema is the law.

6. **Tiered autonomy.** Simple fixes are auto-applied. Complex changes require human approval. The system earns trust through transparency.

7. **12-factor generated apps.** All configuration comes from environment variables. Database backends are swappable. Auth is optional and contract-driven.

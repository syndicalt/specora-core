# Healer System Design

**Date:** 2026-04-07
**Status:** Approved
**Issue:** syndicalt/specora-core#4

## Purpose

The Healer is Specora Core's self-healing subsystem (Tier 3). It detects, diagnoses, and repairs contract errors from two sources: the Forge compilation pipeline (validation/compilation failures) and the generated application at runtime (500s, schema violations, unhandled exceptions). It traces runtime errors back to the source contract, proposes fixes, and applies them with tiered autonomy.

The Healer completes the feedback loop: **contracts → generated code → runtime errors → contract fixes → regeneration**.

## Architecture: Pipeline with SQLite Queue

Five stages connected by a SQLite-backed priority queue:

```
  ┌──────────────────────────────────────────────────────────┐
  │                      INTAKE                               │
  │  HTTP API (POST /healer/ingest)  +  File Watcher (.inbox) │
  └──────────────────┬───────────────────────────────────────┘
                     │ HealerTicket
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                     QUEUE (SQLite)                        │
  │  Priority-ordered. Statuses: queued → analyzing →         │
  │  proposed → approved → applied | failed | rejected        │
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                    ANALYZER                               │
  │  Classify error → assign tier (1/2/3) + priority          │
  │  Infer contract_fqn from stacktrace if unknown (LLM)     │
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                    PROPOSER                               │
  │  Tier 1: deterministic (normalize_contract)               │
  │  Tier 2: LLM structural fix (meta-schema + diff history)  │
  │  Tier 3: LLM runtime→contract trace + fix                │
  └──────────────────┬───────────────────────────────────────┘
                     │ HealerProposal
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                    APPLIER                                │
  │  Tier 1: auto-apply + log                                │
  │  Tier 2-3: queue for approval → apply on approve          │
  │  Validate → write → diff (DiffOrigin.HEALER) → rollback  │
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                   NOTIFIER                                │
  │  Console (Rich) + Webhook (POST) + File (.jsonl audit)   │
  └──────────────────┬───────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────────────────────┐
  │                   MONITOR                                 │
  │  Success rates, recurring patterns, metrics API           │
  └──────────────────────────────────────────────────────────┘
```

## Data Models

### HealerTicket

The primary unit of work flowing through the pipeline.

```python
class TicketSource(str, Enum):
    VALIDATION = "validation"       # forge validate errors
    COMPILATION = "compilation"     # forge compile errors
    RUNTIME = "runtime"             # generated app runtime errors
    MANUAL = "manual"               # user-submitted via CLI

class TicketStatus(str, Enum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    PROPOSED = "proposed"           # fix ready, awaiting approval (tier 2-3)
    APPROVED = "approved"           # user approved, applying
    APPLIED = "applied"             # fix applied successfully
    FAILED = "failed"               # fix failed validation or compilation
    REJECTED = "rejected"           # user rejected the proposal

class Priority(str, Enum):
    CRITICAL = "critical"           # Blocks deployment / runtime crash
    HIGH = "high"                   # Validation errors preventing generation
    MEDIUM = "medium"               # Structural issues, warnings
    LOW = "low"                     # Style fixes, optimizations

@dataclass
class HealerTicket:
    id: str                          # UUID
    source: TicketSource
    contract_fqn: str | None         # Known or inferred by Analyzer
    error_type: str                  # "naming", "fqn", "schema", "runtime_500", etc.
    raw_error: str                   # Original error text / stacktrace
    context: dict                    # Request context, file path, env info
    status: TicketStatus
    tier: int                        # 1=deterministic, 2=LLM structural, 3=runtime→contract
    priority: Priority
    proposal: HealerProposal | None
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str             # Why it was applied/failed/rejected
```

### HealerProposal

A proposed fix attached to a ticket.

```python
@dataclass
class HealerProposal:
    contract_fqn: str
    before: dict                     # Contract before fix
    after: dict                      # Contract after fix
    changes: list[FieldChange]       # From forge.diff.tracker.compute_diff
    explanation: str                  # Human-readable explanation of the fix
    confidence: float                # 0.0-1.0, from analyzer/proposer
    method: str                      # "deterministic" | "llm_structural" | "llm_runtime"
```

## Queue: SQLite Backend

Single SQLite database at `.forge/healer/healer.db`.

```sql
CREATE TABLE tickets (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    contract_fqn TEXT,
    error_type TEXT,
    raw_error TEXT NOT NULL,
    context TEXT NOT NULL,            -- JSON
    status TEXT NOT NULL DEFAULT 'queued',
    tier INTEGER,
    priority TEXT NOT NULL DEFAULT 'medium',
    proposal TEXT,                    -- JSON (HealerProposal serialized)
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_note TEXT DEFAULT ''
);

CREATE INDEX idx_status_priority ON tickets(status, priority);
CREATE INDEX idx_contract_fqn ON tickets(contract_fqn);
```

Priority ordering: `CRITICAL > HIGH > MEDIUM > LOW`, then `created_at ASC` within each priority.

The `queue.py` module wraps all SQLite operations and exposes:
- `enqueue(ticket) → str` (returns ID)
- `next_queued() → HealerTicket | None` (fetch highest priority)
- `update_status(id, status, resolution_note="")` 
- `set_proposal(id, proposal)`
- `list_tickets(status=None, priority=None, contract_fqn=None) → list[HealerTicket]`
- `get_ticket(id) → HealerTicket | None`
- `stats() → dict` (counts by status, tier, priority)

## Pipeline Stages

### Stage 1: Intake

**HTTP API** (`POST /healer/ingest`):
```json
{
    "source": "runtime",
    "contract_fqn": "entity/todo_list/task",  // optional
    "error": "TypeError: 'NoneType' object is not subscriptable",
    "stacktrace": "...",
    "context": {
        "request_path": "/api/tasks/123",
        "method": "PATCH",
        "generated_file": "runtime/backend/routes/tasks.py",
        "line": 42
    }
}
```

Returns: `{"ticket_id": "uuid-here", "status": "queued"}`

**File watcher**: Monitors `.forge/healer/inbox/` for `*.json` files. Each file is a serialized intake payload. After processing, moved to `.forge/healer/inbox/processed/`.

**CLI shortcut**: `specora healer fix domains/todo_list` creates tickets from `forge validate` errors directly.

### Stage 2: Analyzer (`healer/analyzer/`)

**`classifier.py`** — Error classification:

| Error Pattern | Type | Tier | Priority |
|---------------|------|------|----------|
| Name doesn't match `^[a-z]...` | `naming` | 1 | high |
| FQN doesn't match `^(entity\|...)...` | `fqn_format` | 1 | high |
| Graph edge doesn't match `^[A-Z]...` | `graph_edge` | 1 | high |
| Missing required field | `missing_field` | 2 | high |
| Invalid enum value | `invalid_enum` | 2 | medium |
| Unresolved reference | `missing_reference` | 2 | high |
| Cycle in dependency graph | `dependency_cycle` | 2 | critical |
| Runtime 500 error | `runtime_500` | 3 | critical |
| Runtime schema violation | `runtime_schema` | 3 | high |
| Runtime unhandled exception | `runtime_exception` | 3 | high |

Reuses `humanize_error()` from `forge.error_display` for validation error classification.

**`tracer.py`** — Runtime error → contract FQN inference:
- Parses stacktrace to find the generated file path
- Maps generated file → source contract using `@generated` provenance headers in generated code
- If header not found, uses LLM with the stacktrace + list of known contracts to infer the source
- Sets `contract_fqn` on the ticket

### Stage 3: Proposer (`healer/proposer/`)

**`deterministic.py`** (Tier 1):
- Loads the contract from disk
- Calls `normalize_contract()` 
- Validates the result
- Creates `HealerProposal` with confidence=1.0, method="deterministic"

**`llm_proposer.py`** (Tier 2-3):
- Builds LLM prompt with:
  - The contract YAML (before)
  - The specific errors
  - Relevant meta-schema rules
  - Diff history for this contract (`store.format_for_llm()`)
  - For Tier 3: stacktrace, generated code snippet, request context
- LLM returns corrected contract YAML
- Parses response, validates the proposed fix
- Creates `HealerProposal` with confidence from LLM assessment, method="llm_structural" or "llm_runtime"

### Stage 4: Applier (`healer/applier.py`)

```
Tier 1 (deterministic, confidence=1.0):
  → Auto-apply: write contract, validate, record diff, notify
  → On failure: rollback, mark ticket "failed"

Tier 2-3 (LLM-proposed):
  → Set ticket status to "proposed"
  → Wait for approval (CLI: `specora healer approve <id>`, HTTP: POST /healer/approve/<id>)
  → On approve: write contract, validate, compile, record diff, notify
  → On reject: mark "rejected" with reason
  → On apply failure: rollback, mark "failed"
```

Rollback: before applying any fix, snapshot the original contract. On failure, restore the snapshot.

Diff recording: every applied fix creates a `ContractDiff` with `origin=DiffOrigin.HEALER` and `origin_detail=f"healer:ticket-{ticket.id}"`.

### Stage 5: Notifier (`healer/notifier.py`)

Three notification channels, all always active:

| Channel | When | Content |
|---------|------|---------|
| Console (Rich) | CLI mode | Formatted ticket status change with colors |
| Webhook | Configurable URL | JSON payload: `{ticket_id, status, contract_fqn, explanation}` |
| File | Always | Append to `.forge/healer/notifications.jsonl` |

Webhook URL configured via `SPECORA_HEALER_WEBHOOK_URL` env var or settings.

### Monitor (`healer/monitor.py`)

Tracks aggregate metrics:
- Success rate by error type (what % of fixes pass re-validation)
- Success rate by tier
- Recurring errors (same contract_fqn + error_type appearing > N times)
- Mean time to resolution

Exposed via `GET /healer/status`:
```json
{
    "queue": {"queued": 3, "proposed": 1, "applied": 42, "failed": 2, "rejected": 1},
    "success_rate": {"tier_1": 0.98, "tier_2": 0.85, "tier_3": 0.72},
    "recurring": [
        {"contract_fqn": "entity/todo_list/task", "error_type": "naming", "count": 5}
    ],
    "recent": [/* last 10 ticket summaries */]
}
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `specora healer fix <path>` | Validate → create tickets → process pipeline (interactive) |
| `specora healer status` | Show queue summary (Rich table) |
| `specora healer tickets` | List all tickets (filterable: `--status`, `--priority`, `--tier`) |
| `specora healer show <id>` | Show ticket detail with proposal diff |
| `specora healer approve <id>` | Approve a proposed fix |
| `specora healer reject <id> --reason "..."` | Reject with reason |
| `specora healer history` | Show applied fixes (from diff store, `DiffOrigin.HEALER`) |
| `specora healer serve` | Start HTTP service (FastAPI, for Docker) |

## HTTP API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /healer/ingest` | POST | Submit error → returns ticket ID |
| `GET /healer/tickets` | GET | List tickets (query: status, priority, tier, contract_fqn) |
| `GET /healer/tickets/{id}` | GET | Ticket detail with proposal |
| `POST /healer/approve/{id}` | POST | Approve proposed fix |
| `POST /healer/reject/{id}` | POST | Reject with reason |
| `GET /healer/status` | GET | Queue health + metrics |
| `GET /healer/health` | GET | Service health check |

## Deployment: Docker Service

Added to the existing `docker-compose.yml`:

```yaml
healer:
  build:
    context: .
    dockerfile: docker/Dockerfile.healer
  ports:
    - "8083:8083"
  volumes:
    - ./domains:/app/domains          # Read/write contracts
    - ./.forge:/app/.forge            # Queue DB + diffs + inbox
  environment:
    - SPECORA_HEALER_PORT=8083
    - SPECORA_HEALER_WEBHOOK_URL=     # Optional
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
  depends_on:
    - backend                          # For runtime error context
```

The Docker service runs:
1. FastAPI HTTP server (intake + management API)
2. File watcher (`.forge/healer/inbox/`)
3. Queue processor loop (poll every 5s for queued tickets)

## Tiered Autonomy

| Tier | Source | Fix Method | Approval | Confidence |
|------|--------|-----------|----------|------------|
| 1 | Validation patterns | `normalize_contract()` | Auto-apply | 1.0 |
| 2 | Structural errors | LLM + meta-schema context | Queue for approval | 0.6-0.9 |
| 3 | Runtime errors | LLM + stacktrace + generated code | Queue for approval | 0.3-0.8 |

## Dependencies on Existing Modules

| Module | Used For |
|--------|----------|
| `forge.normalize` | Tier 1 deterministic fixes |
| `forge.error_display` | Error classification + human-readable messages |
| `forge.parser.validator` | Contract validation (pre/post fix) |
| `forge.parser.loader` | Load contracts from disk |
| `forge.diff.tracker` | Compute diffs for proposals |
| `forge.diff.store` | Record applied fixes, provide LLM context |
| `forge.diff.models` | DiffOrigin.HEALER |
| `engine.engine` | LLM calls for Tier 2-3 fixes |
| `forge.ir.compiler` | Re-compile after fix to catch cascading issues |

## Project Layout

```
healer/
├── __init__.py
├── models.py              # HealerTicket, HealerProposal, enums
├── queue.py               # SQLite-backed priority queue
├── analyzer/
│   ├── __init__.py
│   ├── classifier.py      # Error → tier + type + priority
│   └── tracer.py          # Runtime stacktrace → contract FQN (LLM)
├── proposer/
│   ├── __init__.py
│   ├── deterministic.py   # Tier 1: normalize_contract()
│   └── llm_proposer.py    # Tier 2-3: LLM structural/runtime fixes
├── applier.py             # Validate → write → diff → rollback
├── monitor.py             # Success rates, recurring patterns
├── notifier.py            # Console + webhook + file notifications
├── pipeline.py            # Orchestrate: analyze → propose → apply → notify
├── watcher.py             # File watcher for inbox/
├── api/
│   ├── __init__.py
│   └── server.py          # FastAPI app
└── cli/
    └── commands.py         # Click commands
```

## Testing Strategy

- **Unit tests**: Each pipeline stage independently tested with mock data
- **Integration test**: End-to-end: submit broken contract → verify fix applied + diff recorded
- **Tier 1 test**: Submit all 29 original todo_list errors → verify all auto-fixed
- **Tier 2 test**: Submit structural error (missing reference) → verify LLM proposal generated
- **Queue test**: Priority ordering, status transitions, concurrent access
- **API test**: HTTP endpoint contracts (request/response shapes)

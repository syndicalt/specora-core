# Healer

> **Note**: The primary interface for Specora Core is your LLM coding agent. The LLM calls Healer Python functions directly (`HealerQueue`, `HealerPipeline`). The CLI commands shown below are the equivalent for terminal users. The Healer also runs as a Docker sidecar in the generated app stack, receiving error reports automatically.

The Healer is Specora Core's Tier 3 self-healing system. It watches for errors -- validation failures, compilation errors, runtime exceptions -- and proposes fixes at the contract level. Simple fixes are auto-applied. Complex fixes require human approval. Every fix is tracked as a diff, making contracts smarter over time.

---

## Python API (Primary)

The LLM uses these functions directly:

```python
from healer.queue import HealerQueue
from healer.pipeline import HealerPipeline
from healer.models import TicketStatus

queue = HealerQueue()

# Check overall stats
stats = queue.stats()
# {"by_status": {"queued": 0, "proposed": 2, "applied": 5}, "total": 7}

# List proposed fixes awaiting approval
proposed = queue.list_tickets(status=TicketStatus.PROPOSED)
for t in proposed:
    print(f"{t.id[:8]}: [{t.priority}] {t.contract_fqn}")
    print(f"  Error: {t.raw_error[:80]}")
    if t.proposal:
        print(f"  Fix: {t.proposal.explanation}")

# Approve a fix
pipeline = HealerPipeline(queue=queue)
pipeline.approve_ticket(proposed[0].id)

# Reject a fix
queue.update_ticket(proposed[1].id, status=TicketStatus.REJECTED, resolution_note="Wrong approach")
```

---

## How It Works

The Healer answers a simple question: **when something breaks, which contract should have prevented it, and what change would fix it?**

Instead of patching generated code (which gets overwritten on the next generation), the Healer fixes the contract itself. Then Forge regenerates the code from the corrected contract.

---

## The Pipeline

The Healer processes errors through seven stages:

```
[1. Intake]      Error arrives (CLI, HTTP API, or manual)
     |
     v
[2. Queue]       Ticket created in SQLite queue with priority ordering
     |
     v
[3. Analyzer]    Classify error: assign error_type, tier (1/2/3), priority
     |
     v
[4. Proposer]    Generate a fix proposal
                  Tier 1: deterministic (normalize_contract)
                  Tier 2-3: LLM-powered (structural changes)
     |
     v
[5. Applier]     Apply the proposal to the contract YAML file
                  Tier 1: auto-applied
                  Tier 2-3: queued for human approval
     |
     v
[6. Notifier]    Log to console, JSONL file, optional webhook POST
     |
     v
[7. Monitor]     Track success rates, recurring patterns, metrics
```

### Stage 1: Intake

Errors enter the pipeline from three sources:

| Source | How it works |
|--------|-------------|
| `spc healer fix` | CLI scans contracts, validates, creates tickets for errors |
| HTTP API `POST /healer/ingest` | Remote services POST errors to the Healer service |
| Manual | `TicketSource.MANUAL` -- human creates a ticket directly |

### Stage 2: Queue

The queue is backed by SQLite (`.forge/healer/healer.db`). Tickets are ordered by:
1. Priority (CRITICAL=0, HIGH=1, MEDIUM=2, LOW=3)
2. Creation time (FIFO within each priority)

Schema:

```sql
CREATE TABLE tickets (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    contract_fqn TEXT,
    error_type TEXT DEFAULT '',
    raw_error TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    tier INTEGER DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'medium',
    priority_order INTEGER NOT NULL DEFAULT 2,
    proposal TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_note TEXT DEFAULT ''
);
```

### Stage 3: Analyzer (`healer/analyzer/classifier.py`)

The classifier examines the error and assigns:

- **`error_type`** -- What kind of error (naming, fqn_format, missing_field, runtime_500, etc.)
- **`tier`** -- Fix complexity (1 = deterministic, 2 = structural, 3 = runtime)
- **`priority`** -- Urgency (critical, high, medium, low)

#### Tier 1 Patterns (Deterministic)

These are auto-fixable naming/format violations:

| Pattern | Error Type |
|---------|-----------|
| Does not match `^[a-z][a-z0-9_]*$` | `naming` |
| Does not match FQN format | `fqn_format` |
| Does not match `^[A-Z][A-Z0-9_]*$` | `graph_edge` |
| Does not match `^[A-Z]{2,6}$` | `number_prefix` |

#### Tier 2 Patterns (Structural)

These require LLM analysis:

| Pattern | Error Type |
|---------|-----------|
| `is a required property` | `missing_field` |
| `is not valid under any of the given schemas` | `schema_mismatch` |
| `is not one of` | `invalid_enum` |
| Unresolved reference | `missing_reference` |
| Dependency cycle | `dependency_cycle` |

#### Tier 3 (Runtime)

| Condition | Error Type | Priority |
|-----------|-----------|----------|
| HTTP 500+ | `runtime_500` | CRITICAL |
| Other runtime exception | `runtime_exception` | HIGH |

### Stage 4: Proposer

#### Deterministic Proposer (Tier 1)

File: `healer/proposer/deterministic.py`

Uses `normalize_contract()` to fix naming violations, format issues, and other mechanically fixable problems. Computes a diff between the original and normalized contract:

```python
before = copy.deepcopy(contract)
after = copy.deepcopy(contract)
normalize_contract(after)
changes = compute_diff(before, after)
```

Confidence is always `1.0`. Method is `"deterministic"`.

#### LLM Proposer (Tier 2-3)

File: `healer/proposer/llm_proposer.py`

For structural fixes that require understanding the contract's semantics. The LLM receives:
- The current contract
- The error message and context
- Diff history for this contract (how it has changed before)

Confidence varies. Method is `"llm"`.

Requires an LLM provider to be configured (see [LLM Providers](llm-providers.md)).

### Stage 5: Applier (`healer/applier.py`)

Applies the proposed fix to the contract YAML file on disk. Records a diff in `.forge/diffs/` with:
- Origin: `healer`
- Ticket ID in origin_detail
- Before/after snapshots
- A change contract describing compatibility, migration impact, affected surfaces, and verification expectations

### Stage 6: Notifier (`healer/notifier.py`)

Three notification channels:

| Channel | Description |
|---------|-------------|
| **Console** | Rich-formatted `[healer/event]` messages |
| **File** | JSONL log at `.forge/healer/notifications.jsonl` |
| **Webhook** | HTTP POST to `SPECORA_HEALER_WEBHOOK_URL` if configured |

Webhook payload:

```json
{
    "timestamp": "2026-04-07T12:00:00+00:00",
    "event": "applied",
    "ticket_id": "abc123...",
    "contract_fqn": "entity/inventory/product",
    "status": "applied",
    "tier": 1,
    "priority": "high",
    "message": "Deterministic normalization: metadata.name: 'Product' -> 'product'"
}
```

Events: `queued`, `proposed`, `applied`, `failed`, `rejected`.

### Stage 7: Monitor (`healer/monitor.py`)

Computes metrics from the queue:

- **Success rate by tier** -- What percentage of each tier's tickets result in applied fixes
- **Recurring errors** -- Contracts that keep producing the same error type (top 10)
- **Recent resolutions** -- Last 10 resolved tickets

---

## Tiered Autonomy

| Tier | Fix Type | Approval | Confidence |
|------|---------|----------|------------|
| **1** | Deterministic (naming, format) | Auto-applied | 1.0 |
| **2** | Structural (missing fields, invalid enums) | Human approval required | Varies |
| **3** | Runtime (server errors, exceptions) | Human approval required | Varies |

Tier 1 fixes are applied immediately and the contract is updated on disk. Tier 2-3 fixes are set to `proposed` status and wait for `spc healer approve` or `POST /healer/approve/{id}`.

---

## Ticket Lifecycle

```
QUEUED -> ANALYZING -> PROPOSED -> APPROVED -> APPLIED
                    |           |
                    |           +-> REJECTED
                    |
                    +-> FAILED (no proposal could be generated)
```

| Status | Description |
|--------|-------------|
| `queued` | Waiting in the priority queue |
| `analyzing` | Being classified |
| `proposed` | Fix proposed, awaiting approval (Tier 2-3) |
| `approved` | Approved by human, being applied |
| `applied` | Fix successfully applied to contract |
| `failed` | No fix could be proposed, or application failed |
| `rejected` | Human rejected the proposed fix |

---

## CLI Commands

All Healer CLI commands are under `spc healer`.

### `spc healer fix [PATH]`

Scan contracts, validate, create tickets for errors, and process fixes.

```bash
spc healer fix domains/inventory
```

Expected output:

```
Loaded 6 contracts from domains/inventory
Found 2 errors and 1 warnings
Created 2 healer tickets
Processed 2 tickets

  1 fixes applied automatically
  1 fixes awaiting approval (run: specora healer tickets)
```

Default path: `domains/`

### `spc healer status [--output text|json]`

Show queue statistics.

```bash
spc healer status
```

Expected output:

```
       Healer Queue Status
+-----------+-------+
| Status    | Count |
+-----------+-------+
| queued    |     0 |
| analyzing |     0 |
| proposed  |     1 |
| approved  |     0 |
| applied   |     3 |
| failed    |     0 |
| rejected  |     0 |
+-----------+-------+
| Total     |     4 |
+-----------+-------+
```

JSON output:

```bash
spc healer status --output json
```

```json
{"by_status": {"applied": 3, "proposed": 1}, "total": 4}
```

### `spc healer tickets [--status STATUS] [--priority PRIORITY] [--output text|json]`

List healer tickets with optional filters.

```bash
spc healer tickets --status proposed
```

Expected output:

```
       Healer Tickets (1)
+----------+----------+----------+------+-----------------------------+----------------------------------+
| ID       | Status   | Priority | Tier | Contract                    | Error                            |
+----------+----------+----------+------+-----------------------------+----------------------------------+
| a1b2c3d4 | proposed | high     |    2 | entity/inventory/product    | 'category' is a required propert |
+----------+----------+----------+------+-----------------------------+----------------------------------+
```

Filter options:

| Option | Values |
|--------|--------|
| `--status` | `queued`, `analyzing`, `proposed`, `approved`, `applied`, `failed`, `rejected` |
| `--priority` | `critical`, `high`, `medium`, `low` |
| `--output` | `text` (default), `json` |

### `spc healer show TICKET_ID`

Show detailed information about a ticket. Supports short ID prefixes (minimum 4 characters).

```bash
spc healer show a1b2c3d4
```

Expected output:

```
Ticket: a1b2c3d4-5e6f-7890-abcd-ef1234567890
  Status:    proposed
  Priority:  high
  Tier:      2
  Source:    validation
  Contract:  entity/inventory/product
  Error:     missing_field
  Message:   'category' is a required property
  Created:   2026-04-07 12:00 UTC

Context:
  path: spec.fields
  source_path: domains/inventory/entities/product.contract.yaml

Proposal:
  Method:     llm
  Confidence: 85%
  Explanation: Added missing 'category' field with type 'string' and enum values
  Changes:    1
```

### `spc healer approve TICKET_ID`

Approve a proposed fix and apply it.

```bash
spc healer approve a1b2c3d4
```

Expected output:

```
Approved and applied: a1b2c3d4
```

Only works on tickets with status `proposed`.

### `spc healer reject TICKET_ID [--reason TEXT]`

Reject a proposed fix.

```bash
spc healer reject a1b2c3d4 --reason "Category should be optional, not required"
```

Expected output:

```
Rejected: a1b2c3d4
  Reason: Category should be optional, not required
```

### `spc healer history`

Show applied healer fixes from the diff store.

```bash
spc healer history
```

Expected output:

```
        Healer Fix History (3)
+------------------+-----------------------------+------------------------------------------+---------+
| Date             | Contract                    | Reason                                   | Changes |
+------------------+-----------------------------+------------------------------------------+---------+
| 2026-04-07 12:05 | entity/inventory/product    | Deterministic normalization: metadata.na… |       1 |
| 2026-04-07 11:30 | entity/inventory/warehouse  | Added missing 'location' field           |       2 |
| 2026-04-06 09:15 | workflow/inventory/product_… | Fixed invalid transition target           |       1 |
+------------------+-----------------------------+------------------------------------------+---------+
```

### `spc healer serve [--port PORT] [--host HOST]`

Start the Healer HTTP service.

```bash
spc healer serve --port 8083
```

Expected output:

```
Starting Healer service on 0.0.0.0:8083
INFO:     Uvicorn running on http://0.0.0.0:8083 (Press CTRL+C to quit)
```

Default: `--host 0.0.0.0 --port 8083`

---

## HTTP API

The Healer exposes a FastAPI HTTP service for remote integration.

### `GET /healer/health`

Health check.

```bash
curl http://localhost:8083/healer/health
```

Response:

```json
{"status": "ok", "service": "healer"}
```

### `GET /healer/status`

Queue metrics: status counts, success rates, recurring errors, recent resolutions.

```bash
curl http://localhost:8083/healer/status
```

Response:

```json
{
    "queue": {"applied": 3, "proposed": 1},
    "success_rate": {"tier_1": 1.0, "tier_2": 0.67},
    "recurring": [
        {"contract_fqn": "entity/inventory/product", "error_type": "naming", "count": 3}
    ],
    "recent": [
        {"id": "a1b2c3d4", "fqn": "entity/inventory/product", "status": "applied", "tier": 1}
    ]
}
```

### `POST /healer/ingest`

Submit an error for healing.

```bash
curl -X POST http://localhost:8083/healer/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "source": "validation",
    "contract_fqn": "entity/inventory/product",
    "error": "'category' is a required property",
    "stacktrace": null,
    "context": {"path": "spec.fields"}
  }'
```

Request body:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | yes | `validation`, `compilation`, `runtime`, or `manual` |
| `contract_fqn` | string | no | FQN of the affected contract |
| `error` | string | yes | Error message |
| `stacktrace` | string | no | Full stack trace |
| `context` | object | no | Additional context (path, status_code, etc.) |

Response:

```json
{"ticket_id": "a1b2c3d4-...", "status": "applied"}
```

The status reflects the state after processing. Tier 1 tickets may already be `applied`. Tier 2-3 will be `proposed`.

### `GET /healer/tickets`

List tickets with optional filters.

```bash
curl "http://localhost:8083/healer/tickets?status=proposed&priority=high"
```

Query parameters:

| Parameter | Values |
|-----------|--------|
| `status` | `queued`, `analyzing`, `proposed`, `approved`, `applied`, `failed`, `rejected` |
| `priority` | `critical`, `high`, `medium`, `low` |
| `contract_fqn` | Exact FQN match |

Response: Array of ticket objects.

### `GET /healer/tickets/{ticket_id}`

Get a single ticket by ID.

```bash
curl http://localhost:8083/healer/tickets/a1b2c3d4-5e6f-7890-abcd-ef1234567890
```

Returns 404 if not found.

### `POST /healer/approve/{ticket_id}`

Approve and apply a proposed fix.

```bash
curl -X POST http://localhost:8083/healer/approve/a1b2c3d4-5e6f-7890-abcd-ef1234567890
```

Response:

```json
{"ticket_id": "a1b2c3d4-...", "status": "approved"}
```

Returns 400 if the ticket is not in `proposed` status. Returns 404 if ticket not found.

### `POST /healer/reject/{ticket_id}`

Reject a proposed fix.

```bash
curl -X POST http://localhost:8083/healer/reject/a1b2c3d4-5e6f-7890-abcd-ef1234567890 \
  -H "Content-Type: application/json" \
  -d '{"reason": "Field should be optional"}'
```

Response:

```json
{"ticket_id": "a1b2c3d4-...", "status": "rejected"}
```

---

## Data Storage

### SQLite Queue

Location: `.forge/healer/healer.db`

The queue persists across process restarts. Tickets retain their full history including proposals and resolution notes.

Indexes:
- `idx_status_priority` on `(status, priority_order, created_at)` -- for efficient `next_queued()` lookup
- `idx_contract_fqn` on `(contract_fqn)` -- for filtering by contract

### Notification Log

Location: `.forge/healer/notifications.jsonl`

Append-only JSONL file. Each line is a JSON object with timestamp, event, ticket_id, contract_fqn, status, tier, priority, and message.

### Diff Store

Location: `.forge/diffs/`

Every applied fix creates a diff record with full before/after contract snapshots. Each diff also includes a change contract so the Healer and Advisor can reason about compatibility, migration risk, affected surfaces, and what verification should run. These diffs feed the LLM proposer with historical context.

---

## Webhook Notifications

Set `SPECORA_HEALER_WEBHOOK_URL` to receive HTTP POST notifications on ticket state changes.

```bash
export SPECORA_HEALER_WEBHOOK_URL=https://your-server.com/webhooks/healer
```

Payload:

```json
{
    "timestamp": "2026-04-07T12:00:00+00:00",
    "event": "applied",
    "ticket_id": "a1b2c3d4-5e6f-7890-abcd-ef1234567890",
    "contract_fqn": "entity/inventory/product",
    "status": "applied",
    "tier": 1,
    "priority": "high",
    "message": "Deterministic normalization: metadata.name: 'Product' -> 'product'"
}
```

Events that trigger webhooks: `queued`, `proposed`, `applied`, `failed`, `rejected`.

The webhook uses `httpx` with a 5-second timeout. Failures are logged but do not block the pipeline.

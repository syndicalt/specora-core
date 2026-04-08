# The Self-Healing Loop

The self-healing loop is Specora Core's signature capability. When something breaks -- a validation failure, a compilation error, a runtime 500 -- the Healer traces the error back to the contract that should have prevented it, proposes a fix at the contract level, applies it, regenerates all code, and notifies you. Generated code is never patched directly. The contract is the fix point, and code follows.

This document covers the complete pipeline, every stage, every option, with Python API examples throughout.

---

## The Complete Flow

```
Error
  |
  v
Ingest (HTTP, CLI, or Python API)
  |
  v
Classify (contract fixable? generator bug? data issue?)
  |
  v
Propose (Tier 1: deterministic, Tier 2-3: LLM)
  |
  v
Approve (Tier 1: auto, Tier 2-3: human via HTML page, Discord link, or API)
  |
  v
Apply (write contract + rollback on failure)
  |
  v
Regenerate (all 4 generators: FastAPI, Postgres, Migrations, Next.js)
  |
  v
Notify (console + file + Discord/Slack/Teams webhooks)
```

Every error enters as a **HealerTicket**. Tickets flow through stages: `queued` -> `analyzing` -> `proposed` -> `approved` -> `applied` (or `failed`/`rejected` at any point).

---

## Stage 1: Ingest

Errors enter the pipeline from three sources:

### 1a. Automatic Runtime Reporting (Generated App)

The generated FastAPI app includes a global exception handler that auto-reports unhandled exceptions to the Healer sidecar:

```python
# This is auto-generated in backend/app.py:
@app.exception_handler(Exception)
async def healer_error_reporter(request, exc):
    # Maps the request path to a contract FQN, posts to /healer/ingest
```

Set `SPECORA_HEALER_URL=http://healer:8083` in the generated app's environment. Every 500 error is automatically ingested.

### 1b. HTTP API

```bash
curl -X POST http://localhost:8083/healer/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "source": "runtime",
    "contract_fqn": "entity/helpdesk/ticket",
    "error": "KeyError: resolution",
    "stacktrace": "Traceback (most recent call last)...",
    "context": {"request_path": "/tickets/abc", "method": "PATCH", "status_code": 500}
  }'
```

Response:

```json
{"ticket_id": "a1b2c3d4-...", "status": "applied"}
```

The status in the response reflects the final state after synchronous processing. Tier 1 fixes are already applied by the time the response comes back.

### 1c. Python API

```python
from healer.queue import HealerQueue
from healer.pipeline import HealerPipeline
from healer.models import HealerTicket, TicketSource

queue = HealerQueue()
pipeline = HealerPipeline(queue=queue)

ticket = HealerTicket(
    source=TicketSource.VALIDATION,
    raw_error="'resolution' is a required property",
    contract_fqn="entity/helpdesk/ticket",
    context={"path": "spec.fields.resolution.required"},
)
queue.enqueue(ticket)
pipeline.process_next()
```

### Ticket Sources

| Source | Value | Description |
|--------|-------|------------|
| Validation | `"validation"` | Contract validation errors from `validate_contract()` |
| Compilation | `"compilation"` | IR compiler errors (unresolved references, cycles) |
| Runtime | `"runtime"` | HTTP 500s and unhandled exceptions from the running app |
| Manual | `"manual"` | Manually submitted errors |

---

## Stage 2: Classify

Classification determines three things: **error type**, **tier** (autonomy level), and **fixable by** (contract, generator, or data).

### Error Classification Logic

The classifier (`healer/analyzer/classifier.py`) uses pattern matching on the error message.

**Tier 1 -- Deterministic (auto-apply, no LLM needed)**

| Pattern | Error Type | Example |
|---------|-----------|---------|
| `does not match '^[a-z][a-z0-9_]*$'` | `naming` | Field name `myField` should be `my_field` |
| `does not match '^(entity\|workflow...` | `fqn_format` | FQN `Entity/helpdesk/ticket` should be `entity/helpdesk/ticket` |
| `does not match '^[A-Z][A-Z0-9_]*$'` | `graph_edge` | Graph edge `assigned_to` should be `ASSIGNED_TO` |
| `does not match '^[A-Z]{2,6}$'` | `number_prefix` | Number prefix `ticket` should be `TKT` |

**Tier 2 -- Structural (LLM-proposed, approval required)**

| Pattern | Error Type | Example |
|---------|-----------|---------|
| `is a required property` | `missing_field` | Entity missing a required field |
| `is not valid under any of the given schemas` | `schema_mismatch` | Field definition doesn't match any valid schema |
| `is not one of` | `invalid_enum` | Invalid enum value |
| `unresolved reference` | `missing_reference` | Contract references a non-existent entity |
| `cycle` | `dependency_cycle` | Circular dependency detected |

**Tier 3 -- Runtime (LLM-proposed, approval required)**

| Pattern | Error Type | Priority |
|---------|-----------|----------|
| HTTP 500 | `runtime_500` | CRITICAL |
| Other runtime | `runtime_exception` | HIGH |

### Fixable-By Triage

Not every error can be fixed by changing a contract. The classifier detects:

**Generator bugs** (not fixable by contract):
- `invalid UUID`
- `column does not exist`
- `syntax error at or near`
- `ImportError` / `ModuleNotFoundError`
- `NameError` / `AttributeError`

**Data issues** (not fixable by contract):
- `duplicate key value violates unique constraint`
- `foreign key violates`
- `null value in column violates not-null`
- `connection refused` / `timeout`

When an error is classified as a generator bug or data issue, the ticket is immediately set to `FAILED` with a diagnostic message, and a webhook notification is sent. The Healer does not attempt to propose a contract fix.

```python
from healer.analyzer.classifier import classify_raw_error

result = classify_raw_error(
    source="runtime",
    error="column 'severity' does not exist",
    context={"status_code": 500}
)
print(result.fixable_by)   # "generator"
print(result.tier)          # 3
print(result.priority)      # Priority.CRITICAL
```

---

## Stage 3: Propose

### Tier 1: Deterministic Proposer

For Tier 1 errors (naming, FQN format, graph edges, number prefixes), the proposer calls `normalize_contract()` on the existing contract. This function deterministically corrects:

- Field names to `snake_case`
- FQNs to lowercase
- Graph edge names to `SCREAMING_SNAKE_CASE`
- Number prefixes to uppercase

No LLM is involved. Confidence is 1.0.

```python
from healer.proposer.deterministic import propose_deterministic_fix

proposal = propose_deterministic_fix(
    contract_fqn="entity/helpdesk/ticket",
    contract={"spec": {"fields": {"myField": {"type": "string"}}}},
)
print(proposal.explanation)
# "Deterministic normalization: spec.fields.myField -> spec.fields.my_field"
print(proposal.confidence)  # 1.0
print(proposal.method)      # "deterministic"
```

### Tier 2-3: LLM Proposer

For structural and runtime errors, the LLM proposer (`healer/proposer/llm_proposer.py`) generates a fix.

**The LLM receives:**
1. The contract FQN
2. The raw error message
3. The current contract YAML
4. Recent change history (last 5 diffs from `DiffStore`)
5. For Tier 3: the runtime stacktrace

**The prompt instructs the LLM to:**
- Return the complete contract as a YAML code block
- Only change what is needed to fix the error
- Respect the valid field property whitelist: `type`, `required`, `description`, `enum`, `default`, `immutable`, `computed`, `constraints`, `references`, `format`, `items_type`
- Respect the valid constraint sub-keys: `min`, `max`, `maxLength`, `minLength`, `pattern`
- Never add invented properties like `required_when` or `conditional_required`

**Sanitization:** After the LLM responds, the proposer strips any invalid properties the LLM may have invented. Every field property and constraint sub-key is checked against the whitelist. Invalid ones are silently removed.

**Validation:** The sanitized contract is validated using `validate_contract()`. If validation errors remain, the proposal is rejected.

**Retry:** If the first attempt fails (parse error, validation error, or no changes), a second attempt is made with a simpler prompt.

**Confidence scores:**
- Tier 2 (structural): 0.7
- Tier 3 (runtime): 0.5

```python
from healer.proposer.llm_proposer import propose_llm_fix
from healer.models import HealerTicket, TicketSource

ticket = HealerTicket(
    source=TicketSource.RUNTIME,
    raw_error="KeyError: 'resolution'",
    contract_fqn="entity/helpdesk/ticket",
    context={"stacktrace": "...", "status_code": 500},
    tier=3,
)
contract = {"apiVersion": "specora.dev/v1", "kind": "Entity", "spec": {"fields": {}}}

proposal = propose_llm_fix(ticket, contract)
if proposal:
    print(proposal.explanation)
    print(proposal.method)       # "llm_runtime" for tier 3, "llm_structural" for tier 2
    print(proposal.confidence)   # 0.5 for tier 3
```

---

## Stage 4: Approve

### Tiered Autonomy

| Tier | Fix Method | Approval | Confidence |
|------|-----------|----------|------------|
| 1 | Deterministic (`normalize_contract`) | Auto-apply | 1.0 |
| 2 | LLM structural | Human approval required | 0.7 |
| 3 | LLM runtime | Human approval required | 0.5 |

Tier 1 fixes are applied immediately after proposal with no human intervention. Tier 2 and Tier 3 fixes are set to `proposed` status and a webhook notification is sent with a link to the approval page.

### The HTML Approval Page

When a Tier 2 or 3 fix is proposed, the webhook notification includes a link:

```
http://localhost:8083/healer/tickets/{ticket_id}/view
```

This renders a full HTML page showing:

- **Header**: Ticket ID, status badge (color-coded), priority badge
- **Metadata**: Tier, source type
- **Contract**: The affected contract FQN
- **Error**: The original error message in a red-bordered box
- **Proposed Fix**: Green-bordered box with the explanation, each change shown in monospace (e.g., `modified: spec.fields.resolution.required = true`), confidence score, and method
- **Action Buttons** (only when status is `proposed`):
  - **Approve Fix** (green button) -- applies the fix, regenerates code, redirects back to the page
  - **Reject** (red button) -- rejects with "Rejected via web UI", redirects back

The page is fully self-contained HTML with inline styles. No JavaScript frameworks. Works in any browser.

### Approve via API

```python
pipeline.approve_ticket("a1b2c3d4-...")
```

```bash
curl -X POST http://localhost:8083/healer/approve/a1b2c3d4-...
```

### Reject via API

```python
pipeline.reject_ticket("a1b2c3d4-...", reason="Wrong approach, need to add a workflow guard instead")
```

```bash
curl -X POST http://localhost:8083/healer/reject/a1b2c3d4-... \
  -H "Content-Type: application/json" \
  -d '{"reason": "Wrong approach"}'
```

---

## Stage 5: Apply

The applier (`healer/applier.py`) writes the corrected contract to disk.

**Steps:**
1. Read the original contract file content
2. Write the proposed `after` YAML to the contract file
3. Validate the written contract
4. If validation fails: **rollback** -- restore the original content, return failure
5. If validation passes: save a diff record to `DiffStore` with origin `HEALER`
6. Return success

```python
from healer.applier import apply_fix

result = apply_fix(
    proposal=proposal,
    contract_path=Path("domains/helpdesk/entities/ticket.contract.yaml"),
    diff_root=Path(".forge/diffs"),
    ticket_id="a1b2c3d4",
)
print(result.success)  # True
print(result.error)    # "" on success, error message on failure
```

Rollback is atomic -- if the new contract fails validation, the file is restored to its exact previous content. No partial writes.

---

## Stage 6: Auto-Regenerate

After a successful apply, the pipeline automatically regenerates all code from the updated contracts.

**Generators invoked:**
1. `FastAPIProductionGenerator` -- backend routes, models, repositories, auth
2. `PostgresGenerator` -- DDL schema
3. `MigrationGenerator` -- incremental ALTER TABLE migrations
4. `NextJSGenerator` -- frontend pages, components, API client

The regeneration compiles contracts to IR, runs all 4 generators, and writes the output files. The notification includes the count of regenerated files.

```python
# This happens automatically inside the pipeline. Manual equivalent:
from forge.ir.compiler import Compiler
from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator

ir = Compiler(contract_root=Path("domains/helpdesk")).compile()
for f in FastAPIProductionGenerator().generate(ir):
    path = Path("runtime") / f.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f.content)
```

---

## Stage 7: Notify

See [Webhooks](webhooks.md) for full details on notification channels.

Every stage transition sends a notification to all configured channels:

| Event | Icon | When |
|-------|------|------|
| `proposed` | `[proposed]` | A fix has been proposed and awaits approval |
| `applied` | `[applied]` | A fix was applied and code regenerated |
| `failed` | `[failed]` | Classification, proposal, or apply failed |
| `rejected` | `[rejected]` | A human rejected the proposed fix |

Notifications go to:
- **Console** (always, via Rich)
- **File** (always, JSONL at `.forge/healer/notifications.jsonl`)
- **Webhooks** (if `SPECORA_HEALER_WEBHOOK_URL` is set -- Discord, Slack, Teams, or raw JSON)

---

## Example: The Resolution Field Fix

This was the first live demo of the self-healing loop. A runtime error occurs because the `ticket` entity contract is missing a `resolution` field that the workflow's side effects try to set.

**1. Error occurs:**
```
PATCH /tickets/abc → 500: KeyError: 'resolution'
```

**2. Generated app auto-reports to Healer:**
```json
{
  "source": "runtime",
  "contract_fqn": "entity/helpdesk/ticket",
  "error": "KeyError: 'resolution'",
  "stacktrace": "...",
  "context": {"status_code": 500}
}
```

**3. Classifier determines:**
- Error type: `runtime_500`
- Tier: 3 (runtime, LLM-proposed)
- Priority: CRITICAL
- Fixable by: `contract` (not a generator bug or data issue)

**4. LLM proposes:** Add a `resolution` field to the ticket entity:
```yaml
resolution:
  type: text
  description: "Resolution notes"
```

**5. Discord webhook fires:**
```
[proposed] Specora Healer -- PROPOSED

Contract: entity/helpdesk/ticket
Priority: critical | Tier: 3

Add 'resolution' field (type: text) to fix KeyError on PATCH.

[View ticket](http://localhost:8083/healer/tickets/abc.../view)
```

**6. Human clicks link, reviews the HTML page, clicks Approve.**

**7. Healer applies the fix:**
- Writes updated contract
- Validates
- Saves diff with origin `HEALER`
- Regenerates all code (FastAPI, Postgres migration, Next.js form)
- Sends `[applied]` webhook

**8. The PATCH /tickets/abc endpoint now works.** The new `resolution` field has a column in Postgres, a form input in the frontend, and a column in the data table.

---

## Example: The Severity Field Added by the Healer

A different scenario: the contract has a `priority` field but the workflow references a `severity` field that does not exist.

**1. Compilation error:**
```
Unresolved reference: 'severity' in workflow guard for entity/helpdesk/incident
```

**2. Classifier determines:**
- Error type: `missing_reference`
- Tier: 2 (structural, LLM-proposed)
- Fixable by: `contract`

**3. LLM proposes:** Add a `severity` field with the same enum as `priority`:
```yaml
severity:
  type: string
  required: true
  enum: [critical, high, medium, low]
  description: "Incident severity level"
```

**4. After approval:** Contract updated, code regenerated, frontend gets a new dropdown, database gets a new column via migration.

---

## The HealerTicket Lifecycle

```
QUEUED ──> ANALYZING ──> PROPOSED ──> APPROVED ──> APPLIED
                │             │                       
                │             └──> REJECTED            
                │                                      
                └──> FAILED (unfixable, generator bug, data issue, no proposal)
```

### Ticket Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | Auto-generated unique ID |
| `source` | `validation` / `compilation` / `runtime` / `manual` | How the error entered |
| `contract_fqn` | string | e.g., `entity/helpdesk/ticket` |
| `error_type` | string | e.g., `naming`, `missing_field`, `runtime_500` |
| `raw_error` | string | The original error message |
| `context` | dict | Additional context (stacktrace, request path, status code) |
| `status` | enum | Current pipeline stage |
| `tier` | 1, 2, or 3 | Autonomy level |
| `priority` | `critical` / `high` / `medium` / `low` | Processing priority |
| `proposal` | HealerProposal or None | The proposed fix (before/after contract, changes, explanation) |
| `created_at` | datetime | When the ticket was created |
| `resolved_at` | datetime or None | When the ticket reached a terminal state |
| `resolution_note` | string | Explanation of the outcome |

### Priority Ordering

The queue is a SQLite-backed priority queue. Tickets are processed in order:
1. `CRITICAL` (priority_order = 0)
2. `HIGH` (priority_order = 1)
3. `MEDIUM` (priority_order = 2)
4. `LOW` (priority_order = 3)

Within the same priority level, FIFO ordering by `created_at`.

---

## HTTP API Reference

The Healer runs as a FastAPI service, typically on port 8083.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healer/health` | GET | Health check: `{"status": "ok", "service": "healer"}` |
| `/healer/status` | GET | Queue stats, success rates by tier, recurring errors, recent tickets |
| `/healer/ingest` | POST | Submit an error. Body: `IngestRequest` |
| `/healer/tickets` | GET | List tickets. Query params: `status`, `priority`, `contract_fqn` |
| `/healer/tickets/{id}` | GET | Get ticket detail as JSON |
| `/healer/tickets/{id}/view` | GET | HTML ticket page with approve/reject buttons |
| `/healer/approve/{id}` | POST | Approve a proposed fix (JSON response) |
| `/healer/approve/{id}/action` | POST | Approve via HTML form (redirects to view) |
| `/healer/reject/{id}` | POST | Reject a proposed fix. Optional body: `{"reason": "..."}` |
| `/healer/reject/{id}/action` | POST | Reject via HTML form (redirects to view) |

### Status Endpoint Response

```json
{
  "queue": {"queued": 0, "proposed": 1, "applied": 12, "failed": 2},
  "success_rate": {"tier_1": 1.0, "tier_2": 0.85, "tier_3": 0.67},
  "recurring": [
    {"contract_fqn": "entity/helpdesk/ticket", "error_type": "missing_field", "count": 3}
  ],
  "recent": [
    {"id": "a1b2c3d4", "fqn": "entity/helpdesk/ticket", "status": "applied", "tier": 1}
  ]
}
```

---

## Python API Summary

```python
from healer.queue import HealerQueue
from healer.pipeline import HealerPipeline
from healer.models import HealerTicket, TicketSource, TicketStatus, Priority

# Initialize
queue = HealerQueue()                              # SQLite at .forge/healer/healer.db
pipeline = HealerPipeline(queue=queue)              # Orchestrates classify → propose → apply → notify

# Submit an error
ticket = HealerTicket(source=TicketSource.RUNTIME, raw_error="...", contract_fqn="entity/helpdesk/ticket")
queue.enqueue(ticket)
pipeline.process_next()                             # Process one queued ticket

# Check stats
queue.stats()                                       # {"by_status": {...}, "total": N}

# List tickets
queue.list_tickets()                                # All tickets
queue.list_tickets(status=TicketStatus.PROPOSED)    # Only proposed
queue.list_tickets(priority=Priority.CRITICAL)      # Only critical

# Get a specific ticket
ticket = queue.get_ticket("a1b2c3d4-...")

# Approve / reject
pipeline.approve_ticket("a1b2c3d4-...")
pipeline.reject_ticket("a1b2c3d4-...", reason="...")
```

---

## CLI Usage

```bash
# Start the Healer service
specora healer start --port 8083

# Check status
specora healer status

# List proposed fixes
specora healer list --status proposed

# Approve a fix
specora healer approve <ticket-id>

# Reject a fix
specora healer reject <ticket-id> --reason "Wrong approach"
```

---

## Related Documentation

- [Webhooks](webhooks.md) -- Multi-channel notifications: Discord, Slack, Teams, raw JSON
- [Migrations](migrations.md) -- How schema changes are tracked after Healer fixes
- [Architecture](architecture.md) -- Where the Healer fits in the 5-tier model
- [Healer](healer.md) -- Shorter overview of the Healer pipeline
- [Production Deployment](production-deployment.md) -- Healer as a Docker sidecar

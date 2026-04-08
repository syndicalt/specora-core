# Webhooks

The Healer sends notifications through multiple channels whenever a ticket changes state. Webhooks are the primary way to stay informed about self-healing activity without watching the console. Click a link in Discord, review the proposed fix in your browser, click Approve.

---

## Supported Platforms

| Platform | URL Pattern | Payload Format |
|----------|-------------|---------------|
| **Discord** | `discord.com/api/webhooks/...` | `{"content": "formatted message"}` |
| **Slack** | `hooks.slack.com/services/...` | `{"text": "formatted message"}` |
| **Microsoft Teams** | `webhook.office.com/...` or `outlook.office.com/...` | `{"text": "formatted message"}` |
| **Raw JSON** | Anything else | Full JSON payload (all fields) |

Platform is auto-detected from the webhook URL. No configuration needed beyond the URL itself.

---

## Configuration

### Environment Variable

```bash
export SPECORA_HEALER_WEBHOOK_URL="https://discord.com/api/webhooks/123456789/abcdef..."
```

### Multi-Channel Support

Send notifications to multiple platforms simultaneously using comma-separated URLs:

```bash
export SPECORA_HEALER_WEBHOOK_URL="https://discord.com/api/webhooks/123/abc,https://hooks.slack.com/services/T00/B00/xxx"
```

Every notification goes to every URL in the list.

### Python API

```python
from healer.notifier import Notifier

# Single channel
notifier = Notifier(webhook_url="https://discord.com/api/webhooks/123/abc")

# Multi-channel
notifier = Notifier(webhook_url="https://discord.com/api/webhooks/123/abc,https://hooks.slack.com/services/T00/B00/xxx")

# From environment variable (reads SPECORA_HEALER_WEBHOOK_URL)
notifier = Notifier()
```

---

## How to Set Up Each Platform

### Discord

1. Open your Discord server, go to **Server Settings > Integrations > Webhooks**
2. Click **New Webhook**
3. Choose a channel (e.g., `#specora-healer`)
4. Name it "Specora Healer"
5. Click **Copy Webhook URL**
6. Set the URL:
   ```bash
   export SPECORA_HEALER_WEBHOOK_URL="https://discord.com/api/webhooks/123456789/abcdefghijk..."
   ```

### Slack

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Create a new app or select an existing one
3. Enable **Incoming Webhooks**
4. Click **Add New Webhook to Workspace**
5. Select a channel (e.g., `#specora-healer`)
6. Copy the webhook URL
7. Set the URL:
   ```bash
   export SPECORA_HEALER_WEBHOOK_URL="https://hooks.slack.com/services/T00000000/B00000000/xxxxxxxxxxxxxxxxxxxxxxxx"
   ```

### Microsoft Teams

1. In your Teams channel, click the `...` menu > **Connectors** (or **Workflows**)
2. Add an **Incoming Webhook** connector
3. Name it "Specora Healer" and click **Create**
4. Copy the webhook URL
5. Set the URL:
   ```bash
   export SPECORA_HEALER_WEBHOOK_URL="https://outlook.office.com/webhook/..."
   ```

### Generic / API Gateway

Any URL that is not Discord, Slack, or Teams receives the raw JSON payload. Use this for:
- Custom notification services
- AWS API Gateway / Lambda
- PagerDuty / Opsgenie integrations
- Logging aggregators

```bash
export SPECORA_HEALER_WEBHOOK_URL="https://my-api.example.com/healer-events"
```

---

## Message Format

### Formatted Message (Discord, Slack, Teams)

All three platforms receive the same Markdown-formatted message:

```
[icon] **Specora Healer -- [EVENT]**

**Contract:** `entity/helpdesk/ticket`
**Priority:** critical | **Tier:** 3

[message content, up to 800 characters]

[link] [View ticket](http://localhost:8083/healer/tickets/abc.../view)
```

The `[View ticket]` link points to the HTML approval page where you can review the proposal and click Approve or Reject.

### Raw JSON (Generic Webhooks)

Generic webhooks receive the full payload:

```json
{
  "timestamp": "2025-01-15T10:30:00+00:00",
  "event": "proposed",
  "ticket_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "contract_fqn": "entity/helpdesk/ticket",
  "status": "proposed",
  "tier": 3,
  "priority": "critical",
  "message": "Add 'resolution' field (type: text) to fix KeyError on PATCH."
}
```

---

## Event Types

| Event | Icon | Description |
|-------|------|-------------|
| `queued` | (inbox) | Error received and queued for processing |
| `proposed` | (lightbulb) | Fix proposed, awaiting approval (Tier 2-3 only) |
| `approved` | (thumbs up) | Fix approved by human |
| `applied` | (checkmark) | Fix applied to contract and code regenerated |
| `failed` | (cross) | Processing failed (unfixable, generator bug, data issue) |
| `rejected` | (no entry) | Human rejected the proposed fix |

---

## Example Notifications

### Proposed Fix (Tier 2)

```
(lightbulb) **Specora Healer -- PROPOSED**

**Contract:** `entity/helpdesk/ticket`
**Priority:** high | **Tier:** 2

Add required 'severity' field with enum [critical, high, medium, low]
to match workflow guard requirement.

(link) [View ticket](http://localhost:8083/healer/tickets/a1b2.../view)
```

### Applied Fix (Tier 1, auto-applied)

```
(checkmark) **Specora Healer -- APPLIED**

**Contract:** `entity/helpdesk/ticket`
**Priority:** high | **Tier:** 1

Deterministic normalization: spec.fields.myField -> spec.fields.my_field;
spec.fields.assignedTo.references.graph_edge: 'assigned_to' -> 'ASSIGNED_TO'

(link) [View ticket](http://localhost:8083/healer/tickets/b2c3.../view)
```

### Auto-Regeneration After Apply

When code is regenerated after a fix, a second notification fires:

```
(checkmark) **Specora Healer -- APPLIED**

**Contract:** `entity/helpdesk/ticket`
**Priority:** critical | **Tier:** 3

(cycle) Auto-regenerated: 42 files regenerated

(link) [View ticket](http://localhost:8083/healer/tickets/c3d4.../view)
```

### Failed (Generator Bug)

```
(cross) **Specora Healer -- FAILED**

**Contract:** `entity/helpdesk/ticket`
**Priority:** critical | **Tier:** 3

(gear) Generator bug (not a contract issue): column 'severity' does not exist

(link) [View ticket](http://localhost:8083/healer/tickets/d4e5.../view)
```

### Failed (Data Issue)

```
(cross) **Specora Healer -- FAILED**

**Contract:** `entity/helpdesk/ticket`
**Priority:** high | **Tier:** 3

(disk) Data issue (not a contract issue): duplicate key value violates unique constraint

(link) [View ticket](http://localhost:8083/healer/tickets/e5f6.../view)
```

### Rejected Fix

```
(no entry) **Specora Healer -- REJECTED**

**Contract:** `entity/helpdesk/ticket`
**Priority:** high | **Tier:** 2

Wrong approach, need to add a workflow guard instead

(link) [View ticket](http://localhost:8083/healer/tickets/f6a7.../view)
```

---

## The HTML Ticket View Page

The `[View ticket]` link in every notification leads to:

```
http://localhost:{SPECORA_HEALER_PORT}/healer/tickets/{ticket_id}/view
```

Default port is `8083` (configurable via `SPECORA_HEALER_PORT` environment variable).

The page shows:
- **Status badge** with color coding (yellow=queued, blue=analyzing, cyan=proposed, green=applied/approved, red=failed/rejected)
- **Priority badge** with color coding (red=critical, orange=high, yellow=medium, green=low)
- **Ticket metadata**: ID, tier, source
- **Contract FQN**: the affected contract
- **Error message**: in a red-bordered box
- **Proposed fix** (if available): green-bordered box with explanation, change list (monospace), confidence score, method
- **Approve / Reject buttons** (only when status is `proposed`): clicking Approve applies the fix and regenerates code; clicking Reject marks the ticket as rejected
- **Resolution note** (if resolved): gray box with the outcome

After clicking Approve or Reject, the page redirects back to itself showing the updated status.

---

## Notification Channels Summary

Every notification is always sent to all three channels:

| Channel | Always Active | Description |
|---------|---------------|-------------|
| **Console** | Yes | Rich-formatted colored output via `rich.console` |
| **File** | Yes | JSONL log at `.forge/healer/notifications.jsonl` |
| **Webhooks** | Only if configured | HTTP POST to each URL in `SPECORA_HEALER_WEBHOOK_URL` |

### Console Output Format

```
[green][healer/applied][/green] entity/helpdesk/ticket: Deterministic normalization: spec.fields...
[cyan][healer/proposed][/cyan] entity/helpdesk/ticket: Add 'resolution' field (type: text)...
[red][healer/failed][/red] entity/helpdesk/ticket: Generator bug (not a contract issue)...
```

### File Log Format

Each line in `.forge/healer/notifications.jsonl` is a JSON object:

```json
{"timestamp": "2025-01-15T10:30:00+00:00", "event": "applied", "ticket_id": "a1b2...", "contract_fqn": "entity/helpdesk/ticket", "status": "applied", "tier": 1, "priority": "high", "message": "Deterministic normalization: ..."}
```

---

## Python API

```python
from healer.notifier import Notifier
from healer.models import HealerTicket, TicketSource

# Create notifier (reads SPECORA_HEALER_WEBHOOK_URL from env)
notifier = Notifier()

# Create notifier with explicit webhook URL
notifier = Notifier(webhook_url="https://discord.com/api/webhooks/123/abc")

# Create notifier with custom log path
notifier = Notifier(log_path=Path(".forge/healer/notifications.jsonl"))

# Send a notification
ticket = HealerTicket(source=TicketSource.RUNTIME, raw_error="KeyError: 'resolution'")
notifier.notify(ticket, event="proposed", message="Add 'resolution' field to fix KeyError")
```

The `notify()` method is called automatically by the pipeline. You only need to call it directly for custom notifications.

---

## Related Documentation

- [Self-Healing Loop](self-healing-loop.md) -- The complete pipeline that triggers notifications
- [Healer](healer.md) -- Overview of the Healer system
- [Production Deployment](production-deployment.md) -- Docker setup including webhook configuration

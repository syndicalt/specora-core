# Extending Specora

How to add new contract types and new code generators to Specora Core.

---

## Part 1: Adding a New Contract Type

A contract type (or "kind") defines a new category of specification that Specora can validate, compile, and generate code from. The existing kinds are: Entity, Workflow, Route, Page, Agent, Mixin, Infra.

Adding a new kind requires touching 5 places, in this order:

### Step 1: Define the Meta-Schema

Create `spec/meta/{kind}.meta.yaml`. This is the law — it defines what a valid contract of this kind looks like.

Use JSON Schema draft 2020-12 in YAML format. Reference the envelope structure with `$ref`.

```yaml
# spec/meta/job.meta.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
$id: "https://specora.dev/meta/job"
title: "Specora Job Contract"
description: "Meta-schema for Job contracts — scheduled background tasks"

type: object
required: [apiVersion, kind, metadata, spec]
additionalProperties: false

properties:
  apiVersion:
    const: "specora.dev/v1"
  kind:
    const: "Job"
  metadata:
    $ref: "envelope#/$defs/metadata"
  requires:
    $ref: "envelope#/$defs/requires"
  spec:
    type: object
    required: [schedule, action]
    properties:
      schedule:
        type: string
        description: "Cron expression (e.g., '0 9 * * *')"
      action:
        type: string
        description: "What the job does"
      entity:
        type: string
        description: "Entity FQN this job operates on"
      filter:
        type: object
        description: "Query filter for the entity"
      notify:
        type: string
        enum: [email, webhook, console, none]
        default: none
```

Look at existing meta-schemas in `spec/meta/` for patterns. The envelope (`apiVersion`, `kind`, `metadata`, `requires`) is shared — your kind only defines what goes inside `spec`.

### Step 2: Create the IR Model

Add your IR model to `forge/ir/model.py`. This is the normalized, target-agnostic representation that generators will consume.

```python
# In forge/ir/model.py

class JobIR(BaseModel):
    """A scheduled background job."""
    fqn: str
    name: str
    domain: str
    schedule: str           # Cron expression
    action: str             # What the job does
    entity_fqn: str = ""    # Entity it operates on (optional)
    filter: dict = Field(default_factory=dict)
    notify: str = "none"
```

Then add it to `DomainIR`:

```python
class DomainIR(BaseModel):
    # ... existing fields ...
    jobs: list[JobIR] = Field(default_factory=list)
```

### Step 3: Add the Compiler Method

In `forge/ir/compiler.py`, add a compilation method and register it in the dispatcher.

**Add the compilation method:**

```python
def _compile_job(self, fqn: str, contract: dict) -> JobIR:
    meta = contract.get("metadata", {})
    spec = contract.get("spec", {})
    return JobIR(
        fqn=fqn,
        name=meta.get("name", ""),
        domain=meta.get("domain", ""),
        schedule=spec.get("schedule", ""),
        action=spec.get("action", ""),
        entity_fqn=spec.get("entity", ""),
        filter=spec.get("filter", {}),
        notify=spec.get("notify", "none"),
    )
```

**Register in `_compile_node`:**

```python
def _compile_node(self, node, ir: DomainIR) -> None:
    kind = node.kind
    contract = node.raw

    if kind == "Entity":
        ir.entities.append(self._compile_entity(node.fqn, contract))
    # ... existing kinds ...
    elif kind == "Job":
        ir.jobs.append(self._compile_job(node.fqn, contract))
    else:
        logger.warning("Unknown contract kind '%s' for %s, skipping", kind, node.fqn)
```

### Step 4: Add IR Passes (if needed)

If your new kind needs post-compilation transformations (like how entities need mixin expansion), create a pass in `forge/ir/passes/`.

```python
# forge/ir/passes/job_entity_binding.py

from forge.ir.model import DomainIR

def run(ir: DomainIR) -> DomainIR:
    """Resolve job entity references to actual entities."""
    entity_map = {e.fqn: e for e in ir.entities}
    for job in ir.jobs:
        if job.entity_fqn and job.entity_fqn not in entity_map:
            # Log warning or raise
            pass
    return ir
```

Register it in `forge/ir/passes/__init__.py` in the `run_all_passes` function.

### Step 5: Write a Contract and Test

Create a sample contract:

```yaml
# domains/helpdesk/jobs/daily_digest.contract.yaml
apiVersion: specora.dev/v1
kind: Job
metadata:
  name: daily_digest
  domain: helpdesk
  description: "Send daily ticket summary"
requires:
  - entity/helpdesk/ticket
spec:
  schedule: "0 9 * * *"
  action: aggregate_unresolved_tickets
  entity: entity/helpdesk/ticket
  filter:
    state: [new, assigned, in_progress]
  notify: email
```

Validate it:

```python
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all

contracts = load_all_contracts(Path("domains/helpdesk"))
errors = validate_all(contracts)
# Should be empty
```

Compile it:

```python
from forge.ir.compiler import Compiler

ir = Compiler(contract_root=Path("domains/helpdesk")).compile()
print(ir.jobs)  # [JobIR(fqn='job/helpdesk/daily_digest', ...)]
```

### Step 6: Optionally Add an Emitter

If you want the Factory (LLM) to be able to create contracts of this type, add an emitter in `factory/emitters/`:

```python
# factory/emitters/job_emitter.py

def emit_job(name: str, domain: str, data: dict) -> str:
    contract = {
        "apiVersion": "specora.dev/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": data.get("description", f"Scheduled job: {name}"),
        },
        "requires": [data["entity"]] if data.get("entity") else [],
        "spec": {
            "schedule": data["schedule"],
            "action": data["action"],
            "entity": data.get("entity", ""),
            "filter": data.get("filter", {}),
            "notify": data.get("notify", "none"),
        },
    }
    return yaml.dump(contract, default_flow_style=False, sort_keys=False)
```

### Summary: Adding a Contract Type

| Step | File(s) | What |
|------|---------|------|
| 1 | `spec/meta/{kind}.meta.yaml` | JSON Schema defining valid contracts |
| 2 | `forge/ir/model.py` | IR model class + add list to `DomainIR` |
| 3 | `forge/ir/compiler.py` | `_compile_{kind}` method + register in dispatcher |
| 4 | `forge/ir/passes/` (optional) | Post-compilation transformation |
| 5 | `domains/` + tests | Sample contract + validation test |
| 6 | `factory/emitters/` (optional) | LLM-friendly emitter function |

---

## Part 2: Adding a New Generator

A generator reads the `DomainIR` and produces code files for a specific target platform. Generators are completely isolated — they only import `forge.ir.model` and `forge.targets.base`.

### Step 1: Create the Generator Directory

```
forge/targets/{name}/
├── __init__.py
├── generator.py      # Orchestrator implementing BaseGenerator
├── gen_routes.py     # Sub-generator (optional, for organization)
├── gen_models.py     # Sub-generator (optional)
└── ...
```

### Step 2: Implement BaseGenerator

```python
# forge/targets/express/generator.py

from forge.ir.model import DomainIR
from forge.targets.base import BaseGenerator, GeneratedFile


class ExpressGenerator(BaseGenerator):

    def name(self) -> str:
        return "express"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        files = []
        files.append(self._generate_package_json(ir))
        files.append(self._generate_app(ir))
        for route in ir.routes:
            entity = next((e for e in ir.entities if e.fqn == route.entity_fqn), None)
            if entity:
                files.append(self._generate_route(route, entity))
        return files

    def _generate_package_json(self, ir: DomainIR) -> GeneratedFile:
        content = """{
  "name": "%s-api",
  "version": "0.1.0",
  "dependencies": {
    "express": "^4.18.0"
  }
}""" % ir.domain
        return GeneratedFile(
            path="package.json",
            content=content,
            provenance=f"domain/{ir.domain}",
        )

    def _generate_app(self, ir: DomainIR) -> GeneratedFile:
        # ... generate Express app entry point
        pass

    def _generate_route(self, route, entity) -> GeneratedFile:
        # ... generate route handler
        pass
```

### Key Rules for Generators

1. **Only import `forge.ir.model` and `forge.targets.base`.** Never import the parser, validator, or raw contracts.
2. **Return `GeneratedFile` objects.** Each has a `path` (relative), `content` (full file text), and `provenance` (source FQN).
3. **Use `provenance_header()`** from `forge.targets.base` to mark files as generated.
4. **Iterate the IR lists you care about.** A TypeScript generator reads `ir.entities`. A Docker generator reads `ir.infra`. You don't need to consume everything.

### Step 3: Register the Generator

In `forge/cli/main.py`, add your generator to the registry:

```python
def _get_generators(target_names: tuple[str, ...]) -> list:
    from forge.targets.express.generator import ExpressGenerator

    registry = {
        # ... existing generators ...
        "express": ExpressGenerator,
    }
    # ...
```

Now it's available via CLI: `spc forge generate domains/helpdesk -t express`

### Step 4: Use the IR Type Table

When mapping IR field types to your target language, reference the type table in `forge/ir/model.py`:

```
IR type     | Python        | TypeScript    | PostgreSQL    | Your Target
----------- | ------------- | ------------- | ------------- | -----------
string      | str           | string        | TEXT          | ?
integer     | int           | number        | INTEGER       | ?
number      | float         | number        | NUMERIC       | ?
boolean     | bool          | boolean       | BOOLEAN       | ?
text        | str           | string        | TEXT          | ?
array       | list          | Array<T>      | JSONB         | ?
object      | dict          | Record<K,V>   | JSONB         | ?
datetime    | datetime      | string (ISO)  | TIMESTAMPTZ   | ?
date        | date          | string        | DATE          | ?
uuid        | str           | string        | UUID          | ?
email       | str           | string        | TEXT          | ?
```

### Step 5: Handle State Machines

If your generator produces route handlers, check `entity.state_machine` for entities that have lifecycle workflows:

```python
if entity.state_machine:
    sm = entity.state_machine
    # sm.initial — starting state
    # sm.states — list of StateIR
    # sm.transitions — dict[str, list[str]] (source -> valid targets)
    # sm.guards — list of GuardIR (require_fields, conditions)
    # sm.side_effects — dict[str, list[dict]]
```

Generate a state transition endpoint that validates the transition is allowed before applying it.

### Step 6: Handle Auth

Check `ir.infra` for auth configuration:

```python
auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
if auth_infra:
    roles = auth_infra.config.get("roles", [])
    protected = auth_infra.config.get("protected_routes", [])
    # Generate middleware, token validation, role checking
```

### Step 7: Write Tests

Add tests in `tests/test_targets/test_{name}.py` following the existing pattern:

```python
import pytest
from forge.ir.model import DomainIR, EntityIR, FieldIR

@pytest.fixture
def sample_ir() -> DomainIR:
    return DomainIR(
        domain="test",
        entities=[EntityIR(
            fqn="entity/test/task",
            name="task",
            domain="test",
            fields=[FieldIR(name="title", type="string", required=True)],
        )],
    )

class TestExpressGenerator:
    def test_generates_package_json(self, sample_ir):
        from forge.targets.express.generator import ExpressGenerator
        files = ExpressGenerator().generate(sample_ir)
        pkg = next(f for f in files if "package.json" in f.path)
        assert "express" in pkg.content
```

You only need IR fixtures — no YAML, no parser, no file system.

### Summary: Adding a Generator

| Step | File(s) | What |
|------|---------|------|
| 1 | `forge/targets/{name}/` | Create directory + `__init__.py` |
| 2 | `generator.py` | Implement `BaseGenerator` — `name()` + `generate(ir)` |
| 3 | `forge/cli/main.py` | Register in `_get_generators()` registry |
| 4 | Type mapping | Map IR field types to target language types |
| 5 | State machines | Handle `entity.state_machine` in route generation |
| 6 | Auth | Handle `ir.infra` auth config |
| 7 | `tests/test_targets/` | Test with IR fixtures only |

---

## Checklist: Before You Ship

- [ ] Meta-schema validates your sample contracts (run `validate_all`)
- [ ] Compiler produces correct IR (check `ir.summary()` and inspect the new list)
- [ ] Generator only imports `forge.ir.model` and `forge.targets.base`
- [ ] All 167+ existing tests still pass
- [ ] New tests cover your additions
- [ ] Generator registered in CLI registry
- [ ] `CLAUDE.md` updated with new contract kind / generator if user-facing

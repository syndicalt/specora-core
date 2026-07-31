# Extractor

> **Note**: The primary interface for Specora Core is your LLM coding agent. The LLM calls Extractor Python functions directly (`synthesize()`). The CLI commands shown below are the equivalent for terminal users.

The Extractor is Specora Core's Tier 4 reverse-engineering system. It analyzes existing Python and TypeScript codebases, extracts entities, routes, and workflows, and emits `.contract.yaml` files. This lets you onboard existing projects into the contract-driven system without rewriting everything from scratch.

Three properties it is built to hold:

- **It never runs your code, and never uploads it.** Python is read with `ast`; nothing is imported, `eval`'d, or sent to an LLM. There is no network call in the pipeline.
- **It reports what it could not read.** Every unparseable file, oversized file, escaping symlink, and unrecognised declaration lands in `report.warnings` and is shown before you accept anything. A contract set that claims to describe your system must not quietly omit half of it.
- **What it writes validates.** Every contract is checked against its meta-schema before the file is written; if one fails, `EmissionError` is raised and nothing is written.

What it produces is a **starting point that compiles**, not a finished domain. It reads structure, not intent: it cannot tell a domain entity from a well-shaped DTO, so you review and accept each entity before anything is written.

---

## Python API (Primary)

The LLM uses these functions directly:

```python
from pathlib import Path
from extractor.synthesizer import synthesize

report = synthesize(Path("/path/to/existing/codebase"), domain="my_app")
print(report.summary())
# "3 entities, 2 routes, 1 workflow"
# "Scanned 47 files, analyzed 12 (0.3s)"

# Access extracted data
for entity in report.entities:
    print(f"  {entity.name}: {len(entity.fields)} fields, confidence={entity.confidence}")

for route in report.routes:
    print(f"  {route.method} {route.path} -> {route.entity_name}")
```

---

## How It Works

The Extractor runs a 4-pass pipeline:

```
[Pass 1: Scan]        Discover and classify source files by role
     |
     v
[Pass 2: Extract]     Parse model files (Python/TypeScript) and route files
     |
     v
[Pass 3: Cross-Ref]   Resolve relationships, detect workflows, normalize names
     |
     v
[Pass 4: Synthesize]  Build AnalysisReport, deduplicate, present to user
```

After the pipeline runs, you review each extracted entity (accept or skip), and the Extractor writes contract files for the accepted entities.

---

## The 4-Pass Pipeline

### Pass 1: Scan (`extractor/scanner.py`)

Recursively walks the source directory and classifies each file by role:

| Role | What it means |
|------|--------------|
| `model` | Contains data model definitions (Pydantic, SQLAlchemy, dataclasses, TypeScript interfaces) |
| `route` | Contains API route handlers (FastAPI, Express, Django views) |
| `page` | Contains UI page definitions |
| `migration` | Database migration files |
| `config` | Configuration files |
| `test` | Test files |
| `unknown` | Not classified |

**File classification uses two strategies:**

1. **Filename patterns** -- `models.py`, `schemas.py`, `routes.py`, `views.py`, `*model*.py`, `*controller*.ts`, etc.
2. **Content hints** -- If filename matching fails, the scanner reads the first 4 KB looking for patterns like `BaseModel`, `APIRouter`, `Column(`, `interface`, `express.Router`, etc. These hints are loose by design, so files that merely *look* like models get classified as models; the analyzers and the review step are what keep them out of the output.

**Scan bounds.** The scan root is a codebase you did not write, so the walk is bounded on every axis the tree controls. Each is reported when it fires:

| Bound | Default | Override |
|---|---|---|
| File size | 2 MiB | `--max-file-kb` |
| File count | 20,000 | `--max-files` |
| Directory depth | 32 | `ScanLimits(max_depth=...)` |

Symlinked directories are never followed (a self-referential symlink would not terminate). A symlinked file whose real path leaves the scan root is refused: without that check, `app/secrets_model.py -> /etc/passwd` is read as part of "your codebase".

**Skipped directories:**

```
node_modules, .git, __pycache__, .venv, venv, env, .tox, .ruff_cache,
.mypy_cache, .pytest_cache, .next, .nuxt, .svelte-kit, dist, build,
target, vendor, *.egg-info, .eggs, htmlcov, site-packages
```

**Supported file extensions:**

| Extension | Language |
|-----------|----------|
| `.py` | Python |
| `.ts`, `.tsx` | TypeScript |
| `.js`, `.jsx` | JavaScript |
| `.sql` | SQL |
| `.prisma` | Prisma |

### Pass 2: Extract

Language-specific analyzers parse the classified files:

#### Python Models (`extractor/analyzers/python_models.py`)

A pure `ast` reader — deterministic, offline, and never executing the file.

Recognises:
- **Pydantic models** (`BaseModel` / `SQLModel` subclasses) -- annotated fields plus `Field(...)` metadata: `description`, `max_length`, `min_length`, `pattern`, `gt`/`ge`/`lt`/`le`, `frozen`, and whether the default makes the field optional.
- **SQLAlchemy models** -- `Column(...)` and `mapped_column(...)`, including `nullable`, `primary_key`, `default`, `comment`, `ForeignKey("table.col")`, `String(n)`, and `Numeric(p, s)`.
- **Dataclasses**, **TypedDict**, **NamedTuple** -- annotated fields. `ClassVar` and `InitVar` are excluded.
- **Enum classes** and `Literal[...]` -- resolved into contract `enum` values. The enum class itself is not emitted as an entity.

Type mapping follows the contract language exactly: `Decimal`/`Numeric` become `decimal` (exact), not `number` (inexact). Conflating them silently rounds money.

`sensitive: true` is set on fields whose names denote credentials (`password_hash`, `api_key`, `refresh_token`, `ssn`, …). Without it the field is built into the generated response model and the API publishes it. Names that are only *about* a credential — `token_count`, `password_expires_at`, `api_key_id` — are not marked.

**Noise it filters** (each reported as a warning):
- DTO projections collapse onto one entity: `TicketCreate`, `TicketUpdate`, `TicketResponse` all become `ticket`, with their fields unioned.
- Result-page envelopes (`items` + a cursor) are not entities.
- Classes named `*Props`, `*Config`, `*Settings`, `*Result`, `*Spec`, `*Error`, … are plumbing, not domain objects.
- A class with fewer than two fields is a request body, not an entity.

**Not recognised**: models built dynamically at runtime, fields added via `__init_subclass__` or metaclasses, inherited fields from a base class in another file, and `Annotated[...]` metadata beyond the first argument.

#### TypeScript Types (`extractor/analyzers/typescript_types.py`)

There is no TypeScript parser available to this process, so this is a brace-matching reader over declaration text. It handles `interface X { ... }` and `type X = { ... }`, resolving member optionality (`?`, `| null`, `| undefined`), string-literal unions into enums, `T[]`/`Array<T>` into `array`, `Record<...>` and nested object literals into `object`, and `Date` into `datetime`. String literals are respected during brace matching, and comments are blanked before parsing.

**Known limits, all reported rather than guessed at:**
- `extends` is not resolved -- inherited members are absent from the output.
- Generic parameters are not instantiated; `Page<Ticket>` reads as `object`.
- Mapped, conditional, and template-literal types are not evaluated.
- Declaration merging across files is not performed.
- A `type X = ...` alias whose right-hand side is not an object literal yields no fields, and says so.
- A declaration with unbalanced braces is skipped, and says so.

#### Routes (`extractor/analyzers/routes.py`)

Python route files are read with `ast`:
- **FastAPI** -- `@router.get(...)` / `@app.post(...)`, with the path prefix from `APIRouter(prefix="/x")` joined on, and `summary=` or the handler docstring as the summary.
- **Flask** -- `@bp.route("/x", methods=[...])`, with `url_prefix` from `Blueprint(...)` joined on.

JavaScript and TypeScript get a narrow regex over `app.<method>("literal")` / `router.<method>("literal")`. Computed paths and chained `.route()` builders are missed, and their presence is reported.

Django's `@api_view` declares methods but not a path, and the URL conf is not read; those endpoints are reported as a gap rather than invented.

The entity name is the first path segment that is not a parameter (`{id}`, `:id`, `<id>`) or a version prefix (`api`, `v1`, …), singularized. A path that names no resource yields no entity name rather than a fabricated one.

### Pass 3: Cross-Reference (`extractor/cross_ref.py`)

Resolves relationships between extracted artifacts:

1. **Normalize names** -- Entity and field names are coerced to `^[a-z][a-z0-9_]*$`. This is a safety boundary, not a cosmetic one: names arrive from a codebase you did not write and go on to become path segments.
2. **Resolve references** -- Fields ending in `_id` are linked to their target entity FQN. A qualifying prefix is dropped if that finds the target (`assigned_agent_id` resolves to `agent`). A reference whose target was **not** extracted is demoted to a plain field and reported -- `emit_entity` copies every reference into `requires`, and a `requires` naming a contract that does not exist fails compilation. A self-reference (`parent_account_id` on `account`) is dropped for the same reason.
3. **Infer graph edges** -- Reference edges derived from field names (e.g., `author_id` produces edge `AUTHOR`)
4. **Choose a display field** -- `references.display` must name a field the *target* actually has, so it is picked from the target's real fields (`name`, `title`, `subject`, `code`, `number`, …) rather than assumed.
5. **Detect workflows** -- Entities with a `state`/`status`/`stage`/`phase` field carrying 2+ enum values get a workflow contract. Where the source declares no transition table, the states are chained in declaration order and the last is marked `terminal`. **This chain is inferred, not observed** -- check it.
6. **Match routes to entities** -- Route entity names normalized to match extracted entities

### Pass 4: Synthesize (`extractor/synthesizer.py`)

Combines all extracted data into an `AnalysisReport`:

```python
@dataclass
class AnalysisReport:
    domain: str
    entities: list[ExtractedEntity]
    routes: list[ExtractedRoute]
    workflows: list[ExtractedWorkflow]
    files_scanned: int
    files_analyzed: int
    warnings: list[str]      # everything the pipeline could not read
```

`warnings` is part of the answer, not a debug channel. A skipped model or a dropped reference means the contract set describes less than it claims, so the CLI prints these under a **Gaps** heading before you accept anything.

Deduplication: If the same entity name appears in multiple files (e.g., `models.py` and `schemas.py`), fields are merged. The first occurrence takes precedence, and new fields from duplicates are added.

---

## The Analysis Report

After extraction, the Extractor presents an interactive report where you accept or skip each entity.

```
--------- Extracting: /path/to/project ---------
  Domain: my_project

  Scanned 47 files, analyzed 12 (0.3s)

--------- Review Entities ----------

  1/4  product  high confidence
  A product entity
  Source: backend/models.py
  Field           Type       Req  Details
  name            string      Y
  sku             string      Y
  price           number
  category_id     string         -> entity/my_project/category
  state           string         enum: draft, active, discontinued

  State machine: state (draft -> active -> discontinued)

  [A]ccept / [S]kip? a
  Accepted

  2/4  category  high confidence
  ...
```

**Confidence levels:**

| Level | Meaning |
|-------|---------|
| `high` | Matched a known model base class or decorator (`BaseModel`, `@dataclass`, a declarative `Base`), or a TypeScript declaration read whole |
| `medium` | Matched on shape alone -- a class that assigns `Column(...)` under a base this analyzer does not know by name |

Confidence describes how sure the analyzer is that it read the *declaration* correctly. It says nothing about whether the class is a domain entity; that judgement is yours.

---

## CLI Usage

### Basic extraction

```bash
spc extract /path/to/existing/project
```

The domain name is auto-inferred from the directory name.

### Specify domain name

```bash
spc extract /path/to/project --domain inventory
```

### Specify output directory

```bash
spc extract /path/to/project --domain inventory --output domains/
```

Default output: `domains/`

### Bound the scan

```bash
spc extract /path/to/project --max-file-kb 512 --max-files 5000
```

### Full example

```bash
spc extract ~/projects/my-flask-app --domain flask_app
```

Expected output:

```
--------- Extracting: /home/user/projects/my-flask-app ---------
  Domain: flask_app

  Scanned 34 files, analyzed 8 (0.2s)

--------- Review Entities ----------

  1/3  user  high confidence
  Source: app/models.py
  Field           Type       Req  Details
  email           email       Y
  name            string      Y
  role            string         enum: admin, editor, viewer
  is_active       boolean

  [A]ccept / [S]kip? a
  Accepted

  2/3  post  high confidence
  ...

  3/3  comment  medium confidence
  ...

---
  3/3 entities accepted

  Writing 3 entities (+ routes + pages) to domains/flask_app
  Proceed? [Y/n] y

  domains/flask_app/entities/user.contract.yaml
  domains/flask_app/entities/post.contract.yaml
  domains/flask_app/entities/comment.contract.yaml
  domains/flask_app/routes/users.contract.yaml
  domains/flask_app/routes/posts.contract.yaml
  domains/flask_app/workflows/post_lifecycle.contract.yaml

---
  Wrote 6 contracts to domains/flask_app

  Next steps:
    spc forge validate domains/flask_app
    spc forge generate domains/flask_app
```

---

## Emitted Contracts

For each accepted entity, the Extractor emits:

### Entity Contract

```yaml
apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: product
  domain: inventory
  description: A product entity
requires:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable
spec:
  fields:
    name:
      type: string
      required: true
    sku:
      type: string
      required: true
    price:
      type: number
    category_id:
      type: string
      references:
        entity: entity/inventory/category
        display: name
        graph_edge: CATEGORY
  mixins:
    - mixin/stdlib/timestamped
    - mixin/stdlib/identifiable
```

### Route Contract (auto-generated for each entity)

A standard CRUD route contract is emitted per accepted entity. **The endpoints discovered in your source do not shape it.** `report.routes` tells you what your API actually exposes so you can compare, but the emitted contract is the same six-endpoint CRUD shape regardless. If your API is not CRUD-shaped, the route contract is a starting point to edit, not a description of what you have.

### Workflow Contract (auto-detected)

If an entity has a state field with 2+ values:

```yaml
apiVersion: specora.dev/v1
kind: Workflow
metadata:
  name: product_lifecycle
  domain: inventory
  description: product lifecycle
spec:
  initial: draft
  states:
    draft:
      label: Draft
    active:
      label: Active
    discontinued:
      label: Discontinued
      terminal: true
  transitions:
    draft: [active]
    active: [discontinued]
```

---

## After Extraction

The emitted contracts are a starting point. You should:

1. **Validate**: `spc forge validate domains/{domain}` -- fix any validation errors
2. **Review and refine**: Edit contracts to add descriptions, constraints, guards, etc.
3. **Add missing contracts**: The Extractor finds what it can, but may miss some entities
4. **Generate**: `spc forge generate domains/{domain}` -- produce code from the contracts
5. **Heal**: `spc healer fix domains/{domain}` -- auto-fix any remaining validation issues

---

## Limitations

- **Python and TypeScript only** -- JavaScript is read for route registrations only. SQL and Prisma files are classified and reported, but there is no analyzer for them: a `schema.sql` is often the most authoritative description of a legacy system, and the Extractor currently ignores it.
- **Static analysis** -- The Extractor reads source files; it does not execute or import them, and does not send them anywhere. Models built at runtime, by metaclass, or by `__init_subclass__` will not be found.
- **Inheritance is not resolved** -- Fields a model inherits from a base class in another file are absent from the output, in both Python and TypeScript.
- **Relationship inference is heuristic** -- A field ending in `_id` is assumed to be a foreign key. Usually right, not always. SQLAlchemy `ForeignKey(...)` is exact and is preferred where present.
- **Workflow transitions are inferred** -- Only the *set* of states is read, from an enum. The transitions between them are a linear chain in declaration order, which is a guess. Guards and side effects are never recovered.
- **`text` and `string` are not distinguishable in Python** -- Both are `str`. A long-form column read from Pydantic comes back as `string`. SQLAlchemy `Text` is exact and does come back as `text`.
- **Routes do not shape the emitted route contract** -- see above.
- **No UI extraction** -- Page contracts are emitted mechanically from the entity's field list; nothing is read from your frontend components.
- **It reads structure, not intent** -- A well-shaped DTO, a pagination envelope, or an internal value object looks exactly like a domain entity. The filters above remove the common cases; the accept/skip review exists for the rest. Expect to skip some of what it finds.

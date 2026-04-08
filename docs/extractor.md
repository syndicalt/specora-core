# Extractor

> **Note**: The primary interface for Specora Core is your LLM coding agent. The LLM calls Extractor Python functions directly (`synthesize()`). The CLI commands shown below are the equivalent for terminal users.

The Extractor is Specora Core's Tier 4 reverse-engineering system. It analyzes existing Python and TypeScript codebases, extracts entities, routes, and workflows, and emits `.contract.yaml` files. This lets you onboard existing projects into the contract-driven system without rewriting everything from scratch.

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
2. **Content hints** -- If filename matching fails, the scanner reads the first 500 bytes looking for patterns like `BaseModel`, `APIRouter`, `Column(`, `interface`, `express.Router`, etc.

**Skipped directories:**

```
node_modules, .git, __pycache__, .venv, venv, env, .tox,
.mypy_cache, .pytest_cache, dist, build, .egg-info, .eggs, htmlcov
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

Extracts from:
- **Pydantic models** (`BaseModel` subclasses) -- fields from type annotations
- **SQLAlchemy models** (`Column()` definitions) -- fields with types and constraints
- **Dataclasses** (`@dataclass` decorator) -- fields from type annotations
- **TypedDict** / **NamedTuple** -- fields from type annotations

For each model, extracts:
- Entity name (from class name)
- Fields with types, required status, descriptions
- Enum values (from `Literal` types or explicit enum classes)
- Foreign key references (from field names ending in `_id`)
- State fields (fields named `state` or `status` with enum values)

#### TypeScript Types (`extractor/analyzers/typescript_types.py`)

Extracts from:
- **Interfaces** (`interface Book { ... }`)
- **Type aliases** (`type Book = { ... }`)

#### Routes (`extractor/analyzers/routes.py`)

Extracts from:
- **FastAPI routers** (`@router.get`, `@app.post`, etc.)
- **Express routers** (`router.get`, `app.post`)
- **Django views** (`@api_view`)

For each route, extracts: path, HTTP method, entity name (inferred from path), summary.

### Pass 3: Cross-Reference (`extractor/cross_ref.py`)

Resolves relationships between extracted artifacts:

1. **Normalize names** -- All entity names converted to `snake_case`
2. **Resolve references** -- Fields ending in `_id` are linked to their target entity FQN (`entity/{domain}/{name}`)
3. **Infer graph edges** -- Reference edges derived from field names (e.g., `author_id` produces edge `AUTHOR`)
4. **Detect workflows** -- Entities with a `state` field and 2+ state values get an auto-generated workflow contract
5. **Match routes to entities** -- Route entity names normalized to match extracted entities

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
```

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
| `high` | Clear model definition with explicit types |
| `medium` | Inferred from patterns, may need manual review |
| `low` | Best-effort extraction, likely needs editing |

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

Standard CRUD routes are emitted for accepted entities.

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
  transitions:
    - from: draft
      to: active
    - from: active
      to: discontinued
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

- **Python and TypeScript only** -- JavaScript files are scanned but not deeply analyzed. SQL and Prisma files are classified but not extracted.
- **Static analysis** -- The Extractor reads source files, it does not execute them. Dynamic models (e.g., generated at runtime) will not be found.
- **Relationship inference is heuristic** -- Fields ending in `_id` are assumed to be foreign keys. This is usually correct but not always.
- **Workflow detection requires explicit state fields** -- If state is managed outside the model (e.g., in a separate state machine library), it will not be detected.
- **No UI extraction** -- Pages are not extracted from frontend code. Page contracts must be written manually or generated via the Factory.

# Specora Core CLI Reference

> **Note**: The CLI is optional. The primary interface for Specora Core is your LLM coding agent (Claude Code, Cursor, Windsurf). The LLM reads `CLAUDE.md` and calls Python functions directly -- no CLI needed. The CLI exists for **CI/CD pipelines**, **shell scripts**, and **users who prefer terminal commands**. For every CLI command below, the equivalent Python API call is documented in [CLAUDE.md](../CLAUDE.md).

Complete reference for every command, subcommand, option, and flag in the Specora Core CLI.

---

## Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Entry Points](#entry-points)
4. [Interactive REPL](#interactive-repl)
5. [Root CLI](#root-cli)
6. [Forge Commands](#forge-commands)
   - [forge validate](#forge-validate)
   - [forge compile](#forge-compile)
   - [forge generate](#forge-generate)
   - [forge graph](#forge-graph)
7. [Factory Commands](#factory-commands)
   - [factory new](#factory-new)
   - [factory add](#factory-add)
   - [factory explain](#factory-explain)
   - [factory refine](#factory-refine)
   - [factory chat](#factory-chat)
   - [factory visualize](#factory-visualize)
   - [factory migrate](#factory-migrate)
8. [Healer Commands](#healer-commands)
   - [healer fix](#healer-fix)
   - [healer status](#healer-status)
   - [healer tickets](#healer-tickets)
   - [healer show](#healer-show)
   - [healer approve](#healer-approve)
   - [healer reject](#healer-reject)
   - [healer history](#healer-history)
   - [healer serve](#healer-serve)
9. [Extractor Commands](#extractor-commands)
   - [extract](#extract)
10. [Diff Commands](#diff-commands)
    - [diff history](#diff-history)
    - [diff show](#diff-show)
11. [Utility Commands](#utility-commands)
    - [init](#init)
12. [Environment Variables](#environment-variables)
13. [Configuration](#configuration)
14. [Workflows](#workflows)

---

## Installation

### Prerequisites

- Python 3.10 or later
- pip

### Install from source

```bash
# Clone the repository
git clone https://github.com/syndicalt/specora-core.git
cd specora-core

# Install with all extras (recommended)
pip install -e ".[all]"
```

### Install options

The project defines four optional dependency groups:

| Extra | Installs | Purpose |
|-------|----------|---------|
| `dev` | pytest, ruff | Testing and linting |
| `llm` | openai, anthropic, httpx | LLM-powered Factory and Healer features |
| `healer` | fastapi, uvicorn, httpx | Healer HTTP service |
| `all` | All of the above | Everything |

```bash
# Minimal (Forge only -- no LLM features)
pip install -e .

# With LLM support (Factory commands)
pip install -e ".[llm]"

# With Healer HTTP service
pip install -e ".[healer]"

# Everything
pip install -e ".[all]"
```

### Making `spc` available globally

When installed inside a virtual environment, `spc` only works while that venv is active. To use it from any directory:

**Option A: Install into system Python**

```bash
deactivate                  # exit any active venv
pip install -e "path/to/specora-core[all]"
```

**Option B: PowerShell alias** (add to `$PROFILE`)

```powershell
function spc { python -m forge.cli.main @args }
```

**Option C: Use `python -m` directly**

```bash
python -m forge.cli.main forge validate domains/my_app
python -m forge.cli.main healer fix domains/my_app
```

### Troubleshooting: `ModuleNotFoundError`

If you see `ModuleNotFoundError: No module named 'extractor'` (or `healer`, `factory`, etc.):

1. **Re-install**: `pip install -e ".[all]"` from the specora-core directory. New packages (like extractor) require a reinstall.
2. **Check your venv**: Make sure the venv where specora-core is installed is active.
3. **Set PYTHONPATH** as a fallback: `$env:PYTHONPATH = "C:\path\to\specora-core"` (PowerShell) or `PYTHONPATH=/path/to/specora-core` (bash).

### Verify installation

```bash
spc --help
```

Expected output:

```
Usage: spc [OPTIONS] COMMAND [ARGS]...

  Specora Core -- Contract-Driven Development Engine.

  Build applications from declarative specifications. Run with no arguments to
  start the interactive REPL.

Options:
  -v, --verbose  Enable debug logging
  --help         Show this message and exit.

Commands:
  diff     Contract diff tracking.
  extract  Reverse-engineer a codebase into Specora contracts.
  factory  The Factory -- conversational contract authoring (LLM-powered).
  forge    The compiler and code generation pipeline.
  healer   The Healer -- self-healing contract pipeline.
  init     Scaffold a new domain with starter contracts.
```

---

## Quick Start

Five minutes from zero to working contracts:

```bash
# 1. Install
pip install -e ".[all]"

# 2. Scaffold a new domain
spc init my_app

# 3. Validate contracts
spc forge validate domains/my_app

# 4. Compile to IR (verify it works)
spc forge compile domains/my_app

# 5. Generate code
spc forge generate domains/my_app

# 6. Inspect generated files
ls runtime/
```

For the LLM-powered experience, set an API key and use the Factory:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
spc factory new
```

---

## Entry Points

Two entry points are registered in `pyproject.toml`:

| Command | Entry Point | Notes |
|---------|-------------|-------|
| `specora-core` | `forge.cli.main:cli` | Full name |
| `spc` | `forge.cli.main:cli` | Short alias (recommended) |

You can also invoke the CLI via Python module:

```bash
python -m forge.cli.main [COMMAND] [OPTIONS]
```

All three invocations are identical. This document uses `spc` throughout.

---

## Interactive REPL

### Launching

Run `spc` with no arguments to enter the interactive REPL:

```bash
spc
```

The REPL shows a branded welcome screen with:
- ASCII art logo
- Domain summary (names, entity counts, contract counts)
- Total contract count across all domains

### Prompt

The prompt is a magenta `>` character. Type slash commands or natural language.

### Slash Commands

All REPL slash commands and their mappings:

| Slash Command | CLI Equivalent | Description |
|---------------|---------------|-------------|
| `/validate [path]` | `spc forge validate [path]` | Validate contracts against meta-schemas |
| `/compile [path]` | `spc forge compile [path]` | Compile contracts to IR |
| `/generate [path]` | `spc forge generate [path]` | Generate code from contracts |
| `/graph [path]` | `spc forge graph [path]` | Show dependency graph |
| `/new` | `spc factory new` | Bootstrap a new domain (interview) |
| `/add <kind> -d <domain> -n <name>` | `spc factory add ...` | Add a single contract |
| `/explain <path>` | `spc factory explain <path>` | Explain a contract in plain English |
| `/refine <path> <instruction>` | `spc factory refine ...` | Modify a contract via natural language |
| `/chat [--domain <d>]` | `spc factory chat ...` | Agentic domain conversation |
| `/heal [path]` | `spc healer fix [path]` | Auto-fix validation errors |
| `/status` | `spc healer status` | Healer queue status |
| `/tickets` | `spc healer tickets` | List healer tickets |
| `/history` | `spc healer history` | Healer fix history |
| `/visualize [path]` | `spc factory visualize [path]` | Generate Mermaid diagrams |
| `/migrate <file> -d <domain>` | `spc factory migrate ...` | Import from OpenAPI/SQL/Prisma |
| `/extract <path> [--domain <d>]` | `spc extract ...` | Reverse-engineer codebase into contracts |
| `/help` | N/A | Show the help table |
| `/clear` | N/A | Clear the screen and re-show welcome |
| `/exit` | N/A | Exit the REPL |
| `! <cmd>` | N/A | Run a shell command (e.g., `! ls domains/`) |

### Tab Completion

The REPL provides tab completion for all slash commands. Start typing `/` and press Tab to see available commands.

### Command History

Command history is persisted to `~/.specora/history`. Use the up/down arrow keys to navigate previous commands. History persists across sessions.

### Auto-Suggestions

The REPL shows ghost-text suggestions from your command history as you type (via `AutoSuggestFromHistory`). Press the right arrow key to accept a suggestion.

### Natural Language Routing

Any input that does not start with `/` or `!` is routed through the LLM agent. The agent interprets your intent and maps it to the appropriate CLI command. This requires an LLM provider to be configured (see [Environment Variables](#environment-variables)).

Examples:

```
> validate my library contracts
  (routes to: forge validate domains/library)

> show me the dependency graph
  (routes to: forge graph domains/)

> create a new entity called review in the library domain
  (routes to: factory add entity -d library -n review)
```

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+C` | Cancel current input (does not exit) |
| `Ctrl+D` or `EOF` | Exit the REPL |
| Up/Down arrows | Navigate command history |
| Right arrow | Accept auto-suggestion |
| Tab | Trigger completion |

---

## Root CLI

```
Usage: spc [OPTIONS] COMMAND [ARGS]...

  Specora Core -- Contract-Driven Development Engine.

  Build applications from declarative specifications. Run with no arguments to
  start the interactive REPL.

Options:
  -v, --verbose  Enable debug logging
  --help         Show this message and exit.

Commands:
  diff     Contract diff tracking.
  extract  Reverse-engineer a codebase into Specora contracts.
  factory  The Factory -- conversational contract authoring (LLM-powered).
  forge    The compiler and code generation pipeline.
  healer   The Healer -- self-healing contract pipeline.
  init     Scaffold a new domain with starter contracts.
```

### Global Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-v`, `--verbose` | Flag | `false` | Enable debug-level logging via Rich handler. Shows detailed log output from all subsystems. |
| `--help` | Flag | N/A | Show help message and exit. |

### Behavior with No Subcommand

When invoked with no subcommand (`spc` alone), the CLI launches the interactive REPL. This is controlled by `invoke_without_command=True` on the root group.

---

## Forge Commands

The Forge is the deterministic compiler pipeline. It reads `.contract.yaml` files, validates them, compiles them into an Intermediate Representation (IR), and generates code.

```
Usage: spc forge [OPTIONS] COMMAND [ARGS]...

  The compiler and code generation pipeline.

Options:
  --help  Show this message and exit.

Commands:
  compile   Compile contracts into IR.
  generate  Compile contracts and generate code.
  graph     Display the contract dependency graph.
  validate  Validate all contracts against their meta-schemas.
```

---

### forge validate

Validate all contracts against their meta-schemas.

```
Usage: spc forge validate [OPTIONS] [PATH]

  Validate all contracts against their meta-schemas.

  Checks that every .contract.yaml file conforms to its kind's meta-schema.
  Reports all errors and warnings with human-readable messages and suggested
  fixes.

Options:
  --output [text|json]  Output format
  --help                Show this message and exit.
```

#### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `PATH` | No | `domains/` | Path to a domain directory or the top-level `domains/` directory. All `.contract.yaml` files under this path are discovered and validated. |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--output` | Choice: `text`, `json` | `text` | Output format. `text` produces rich terminal output with colors. `json` produces machine-readable JSON. |

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All contracts are valid (may have warnings) |
| 1 | One or more validation errors found, or contracts could not be loaded |

#### JSON Output Schema

When `--output json` is used:

```json
{
  "valid": true,
  "contract_count": 12,
  "errors": [],
  "warnings": [
    {
      "fqn": "entity/library/book",
      "path": "spec.fields.isbn",
      "message": "Field 'isbn' has no description",
      "severity": "warning"
    }
  ]
}
```

#### Examples

```bash
# Validate all domains
spc forge validate

# Validate a specific domain
spc forge validate domains/library

# Get JSON output for CI pipelines
spc forge validate domains/library --output json

# Validate and pipe to jq
spc forge validate --output json | jq '.errors[]'
```

#### Related Commands

- `spc forge compile` -- Runs validation as part of compilation
- `spc healer fix` -- Auto-fix validation errors

---

### forge compile

Compile contracts into the Intermediate Representation (IR).

```
Usage: spc forge compile [OPTIONS] [PATH]

  Compile contracts into IR.

  Runs the full pipeline: load -> validate -> resolve -> compile -> passes.
  Prints a summary of the compiled IR.

Options:
  --output [text|json]  Output format
  --help                Show this message and exit.
```

#### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `PATH` | No | `domains/` | Path to a domain directory or `domains/`. |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--output` | Choice: `text`, `json` | `text` | Output format. |

#### Pipeline Steps

The compile command executes the full pipeline in order:

1. **Load** -- Discover and parse all `.contract.yaml` files
2. **Validate** -- Check against meta-schemas
3. **Resolve** -- Build the dependency graph, detect cycles
4. **Compile** -- Transform contracts into IR nodes
5. **Passes** -- Run post-compilation passes:
   - Mixin expansion (inject mixin fields into entities)
   - Table name inference (derive PostgreSQL table names)
   - State machine binding (link workflows to entities)
   - Reference resolution (validate cross-entity references)

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Compilation successful |
| 1 | Compilation failed (validation errors, resolution errors, etc.) |

#### JSON Output Schema

```json
{
  "success": true,
  "summary": "4 entities, 1 workflow, 4 routes, 4 pages",
  "entities": 4,
  "workflows": 1,
  "routes": 4,
  "pages": 4
}
```

#### Examples

```bash
# Compile all domains
spc forge compile

# Compile a specific domain
spc forge compile domains/library

# JSON output for tooling
spc forge compile domains/library --output json
```

#### Related Commands

- `spc forge validate` -- Validation only (no compilation)
- `spc forge generate` -- Compile + generate code

---

### forge generate

Compile contracts and generate code for target platforms.

```
Usage: spc forge generate [OPTIONS] [PATH]

  Compile contracts and generate code.

  Runs the full compilation pipeline, then invokes target generators to
  produce code files in the output directory.

Options:
  -t, --target TEXT  Target generators to run
  -o, --output PATH  Output directory for generated files
  --help             Show this message and exit.
```

#### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `PATH` | No | `domains/` | Path to a domain directory or `domains/`. |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-t`, `--target` | Text (repeatable) | `typescript`, `fastapi`, `postgres` | Target generator(s) to run. Can be specified multiple times. |
| `-o`, `--output` | Path | `runtime/` | Output directory for generated files. Created if it does not exist. |

#### Available Targets

| Target | Generator Class | Output |
|--------|----------------|--------|
| `typescript` | `TypeScriptGenerator` | TypeScript interfaces and types |
| `fastapi` | `FastAPIGenerator` | FastAPI route handlers and models |
| `postgres` | `PostgresGenerator` | `CREATE TABLE` DDL statements |

If an unknown target name is provided, it is skipped with a warning listing available targets.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Generation successful |
| 1 | Compilation failed before generation could start |

#### Examples

```bash
# Generate all targets (TypeScript + FastAPI + PostgreSQL)
spc forge generate domains/library

# Generate only TypeScript types
spc forge generate domains/library --target typescript

# Generate only PostgreSQL DDL
spc forge generate domains/library -t postgres

# Multiple specific targets
spc forge generate domains/library -t typescript -t postgres

# Custom output directory
spc forge generate domains/library --output output/

# Generate for all domains
spc forge generate
```

#### Output Structure

Generated files are written to subdirectories under the output directory:

```
runtime/
  backend/
    routes_books.py          # FastAPI route handlers
    routes_authors.py
  frontend/
    types.ts                 # TypeScript interfaces
  database/
    schema.sql               # PostgreSQL DDL
```

Every generated file includes a `@generated` provenance header. Never edit generated files by hand -- change the contracts and regenerate.

#### Related Commands

- `spc forge compile` -- Compile without generating code
- `spc forge validate` -- Validate before generating

---

### forge graph

Display the contract dependency graph.

```
Usage: spc forge graph [OPTIONS] [PATH]

  Display the contract dependency graph.

Options:
  --output [text|json]  Output format
  --help                Show this message and exit.
```

#### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `PATH` | No | `domains/` | Path to a domain directory or `domains/`. |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--output` | Choice: `text`, `json` | `text` | Output format. `text` renders a Rich tree grouped by kind. `json` produces machine-readable JSON. |

#### Text Output

The text output renders a Rich tree grouped by contract kind, showing each contract's FQN and its dependencies (`requires`) and dependents (`used by`).

#### JSON Output Schema

```json
{
  "nodes": [
    {
      "fqn": "entity/library/book",
      "kind": "Entity",
      "dependencies": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
      "dependents": ["route/library/books", "page/library/books"]
    }
  ],
  "count": 12
}
```

#### Examples

```bash
# Show graph for all domains
spc forge graph

# Show graph for a specific domain
spc forge graph domains/library

# JSON output for external tools
spc forge graph domains/library --output json

# Pipe to jq to find contracts with no dependents
spc forge graph --output json | jq '.nodes[] | select(.dependents | length == 0)'
```

#### Related Commands

- `spc factory visualize` -- Generate Mermaid diagrams (ER, state, deps)

---

## Factory Commands

The Factory is the LLM-powered contract authoring system. It uses conversational interviews and natural language to create, explain, modify, and migrate contracts. All Factory commands that use an LLM require a provider to be configured (see [Environment Variables](#environment-variables)).

```
Usage: spc factory [OPTIONS] COMMAND [ARGS]...

  The Factory -- conversational contract authoring (LLM-powered).

Options:
  --help  Show this message and exit.

Commands:
  add        Add a single contract to an existing domain via LLM interview.
  chat       Agentic domain conversation -- discuss, propose, and build contracts.
  explain    Explain a contract in plain English using the LLM.
  migrate    Import external schemas into Specora contracts via LLM.
  new        Bootstrap a new domain from a conversational interview.
  refine     Modify an existing contract via natural language instruction.
  visualize  Generate Mermaid diagrams for contracts.
```

---

### factory new

Bootstrap a complete domain from a multi-phase conversational interview.

```
Usage: spc factory new [OPTIONS]

  Bootstrap a new domain from a conversational interview.

Options:
  --help  Show this message and exit.
```

#### Arguments

None.

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--help` | Flag | N/A | Show help message and exit. |

#### Interview Phases

The `factory new` command runs a structured, multi-phase interview:

1. **Domain Discovery** -- What is this domain about? The LLM asks about the business area, identifies core entities, and establishes naming conventions.
2. **Entity Interviews** -- For each discovered entity, a detailed interview captures fields, types, constraints, relationships (references with graph edges), and mixins.
3. **Workflow Interviews** -- For entities with state machines, the LLM interviews about states, transitions, guards, and side effects.
4. **Emit** -- All interview data is transformed into contract YAML files. Contracts are validated before preview.
5. **Preview** -- All generated contracts are shown in `$EDITOR` (or displayed in-terminal). You can edit them before confirming.
6. **Write** -- Accepted contracts are written atomically to `domains/<name>/`.

#### Session Persistence

If you interrupt the interview (Ctrl+C or Ctrl+D), the session is saved to `.specora/session/`. When you run `factory new` again, you are prompted to resume or start fresh. This means you can stop mid-interview and pick up where you left off.

#### Generated Contract Types

For each entity discovered, the Factory generates:

- `entities/<name>.contract.yaml` -- Entity contract
- `workflows/<name>_lifecycle.contract.yaml` -- Workflow contract (if entity has a state machine)
- `routes/<name>s.contract.yaml` -- Route contract (CRUD endpoints)
- `pages/<name>s.contract.yaml` -- Page contract (UI spec)

#### Requires

- An LLM provider configured via environment variables.

#### Examples

```bash
# Start a new domain interview
spc factory new

# Or via the REPL
> /new
```

#### Related Commands

- `spc init` -- Scaffold a domain without LLM (minimal starter files)
- `spc factory add` -- Add a single contract to an existing domain

---

### factory add

Add a single contract to an existing domain.

```
Usage: spc factory add [OPTIONS] {entity|workflow|route|page}

  Add a single contract to an existing domain via LLM interview.

Options:
  -d, --domain TEXT  Target domain name  [required]
  -n, --name TEXT    Contract name (snake_case)  [required]
  -e, --entity TEXT  Entity FQN (required for route/page)
  --help             Show this message and exit.
```

#### Arguments

| Argument | Required | Choices | Description |
|----------|----------|---------|-------------|
| `KIND` | Yes | `entity`, `workflow`, `route`, `page` | The kind of contract to create. |

#### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `-d`, `--domain` | Text | Yes | N/A | Target domain name. The domain directory must already exist under `domains/`. |
| `-n`, `--name` | Text | Yes | N/A | Contract name in `snake_case`. |
| `-e`, `--entity` | Text | No (required for `route` and `page`) | `""` | Entity FQN that the route or page is for. Format: `entity/<domain>/<name>`. |

#### Behavior by Kind

| Kind | LLM Required | Process |
|------|-------------|---------|
| `entity` | Yes | Runs an entity interview to capture fields, types, constraints, relationships |
| `workflow` | Yes | Runs a workflow interview to capture states, transitions, guards |
| `route` | No | Mechanically emits a CRUD route contract for the referenced entity |
| `page` | No | Mechanically emits a page contract for the referenced entity |

#### Validation

After generation, the contract is validated against meta-schemas. If validation fails, the command exits with code 1 and displays errors.

#### Preview and Confirmation

The generated YAML is displayed with syntax highlighting and line numbers. You are prompted `Write this contract? [Y/n]` before anything is written to disk.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Contract written successfully, or user cancelled |
| 1 | Domain not found, contract already exists, missing `--entity`, validation errors, or LLM configuration error |

#### Examples

```bash
# Add an entity with LLM interview
spc factory add entity -d library -n review

# Add a workflow with LLM interview
spc factory add workflow -d library -n review_lifecycle

# Add a route (mechanical, no LLM needed)
spc factory add route -d library -n reviews -e entity/library/review

# Add a page (mechanical, no LLM needed)
spc factory add page -d library -n reviews -e entity/library/review
```

#### Related Commands

- `spc factory new` -- Create an entire domain at once
- `spc factory refine` -- Modify an existing contract

---

### factory explain

Explain a contract in plain English using the LLM.

```
Usage: spc factory explain [OPTIONS] PATH

  Explain a contract in plain English using the LLM.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `PATH` | Yes | Path to a `.contract.yaml` file. Must exist on disk. |

#### Behavior

1. Loads and parses the contract file.
2. Verifies the file has a `.contract.yaml` extension.
3. Sends the full YAML content to the LLM with a system prompt tuned for explanation.
4. Renders the LLM's response as Markdown in the terminal.

#### Requires

- An LLM provider configured via environment variables.
- The file must end with `.contract.yaml`.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Explanation displayed |
| 1 | File not found, not a contract file, LLM configuration error, or LLM error |

#### Examples

```bash
# Explain an entity contract
spc factory explain domains/library/entities/book.contract.yaml

# Explain a workflow
spc factory explain domains/library/workflows/book_lifecycle.contract.yaml

# Via the REPL
> /explain domains/library/entities/book.contract.yaml
```

#### Related Commands

- `spc factory refine` -- Modify a contract after understanding it

---

### factory refine

Modify an existing contract via natural language instruction.

```
Usage: spc factory refine [OPTIONS] PATH INSTRUCTION

  Modify an existing contract via natural language instruction.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `PATH` | Yes | Path to a `.contract.yaml` file. Must exist on disk. |
| `INSTRUCTION` | Yes | Natural language description of the change to make. |

#### Behavior

1. Loads the current contract from disk.
2. Sends the contract content and the instruction to the LLM.
3. The LLM returns the complete modified contract.
4. The response is parsed, YAML-extracted, and normalized:
   - `metadata.name` is forced to `snake_case`
   - `requires` entries are forced to lowercase FQN format
   - `graph_edge` values are forced to `SCREAMING_SNAKE_CASE`
5. The modified contract is validated against meta-schemas.
6. The modified YAML is displayed with syntax highlighting.
7. You are prompted `Apply this change? [Y/n]`.
8. If accepted, the file is overwritten and a diff is recorded.

#### Diff Tracking

Every accepted refinement produces a `ContractDiff` with:
- Origin: `factory`
- Origin detail: `factory:refine`
- Reason: the instruction you provided
- Stored in `.forge/diffs/`

#### Requires

- An LLM provider configured via environment variables.
- The file must end with `.contract.yaml`.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Change applied or user cancelled |
| 1 | File not found, not a contract file, LLM error, YAML parse failure, or validation errors in the modified contract |

#### Examples

```bash
# Add a field
spc factory refine domains/library/entities/book.contract.yaml "add an isbn field of type string, required"

# Change a relationship
spc factory refine domains/library/entities/book.contract.yaml "change the author reference to use WRITTEN_BY as the graph edge"

# Remove a field
spc factory refine domains/library/entities/book.contract.yaml "remove the subtitle field"

# Via the REPL
> /refine domains/library/entities/book.contract.yaml add a page_count integer field
```

#### Related Commands

- `spc factory explain` -- Understand a contract before modifying it
- `spc diff history` -- View the change history after refining

---

### factory chat

Agentic domain conversation with tool use. The LLM can discuss your domain model and propose concrete changes (entity creation, contract modification, validation).

```
Usage: spc factory chat [OPTIONS]

  Agentic domain conversation -- discuss, propose, and build contracts.

Options:
  -d, --domain TEXT  Domain to chat about
  --help             Show this message and exit.
```

#### Arguments

None.

#### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `-d`, `--domain` | Text | No | Auto-detected | Domain to chat about. If omitted and exactly one domain exists, it is auto-selected. If multiple domains exist, you must specify. |

#### Domain Auto-Detection

- If `domains/` contains exactly one subdirectory, that domain is auto-selected.
- If `domains/` contains zero subdirectories, the command exits with an error suggesting `spc init`.
- If `domains/` contains multiple subdirectories, the command exits with an error listing available domains.

#### Available Tools

The chat agent has three tools it can invoke:

| Tool | Description |
|------|-------------|
| `propose_entity` | Creates a new Entity contract (with auto-generated Route and Page contracts). Shows the proposal for confirmation before writing. |
| `propose_modification` | Modifies an existing contract via natural language. Shows the modification for confirmation before applying. |
| `validate_domain` | Runs validation on all contracts in the current domain. |

Every tool invocation requires your explicit confirmation before any files are modified.

#### Chat Loop

The chat is a persistent conversation. The LLM maintains context across messages. After each tool execution, the domain context is refreshed so the LLM sees the latest contract state.

Type `exit`, `quit`, `/exit`, `/quit`, or press `Ctrl+D` to end the chat.

#### Requires

- An LLM provider configured via environment variables.
- At least one domain to exist under `domains/`.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Chat ended normally |
| 1 | No domains found, multiple domains without `--domain`, or LLM configuration error |

#### Examples

```bash
# Chat about the only domain
spc factory chat

# Chat about a specific domain
spc factory chat -d library

# Via the REPL
> /chat
> /chat --domain library
```

#### Example Conversation

```
> I need a review system where patrons can rate books

  (LLM proposes entity/library/review with fields: rating, comment, patron, book)
  Write this contract? [Y/n] y
  Created entity/library/review with route and page contracts.

> Can you add a date field to the review?

  (LLM proposes modification to entity/library/review)
  Apply this modification? [Y/n] y

> Let's validate everything

  (LLM runs validate_domain)
  All 16 contracts are valid.
```

#### Related Commands

- `spc factory new` -- Full domain bootstrap (structured interview)
- `spc factory add` -- Add a single contract (non-chat)

---

### factory visualize

Generate Mermaid diagrams from contracts.

```
Usage: spc factory visualize [OPTIONS] [PATH]

  Generate Mermaid diagrams for contracts.

Options:
  --type [er|state|deps]  Diagram type
  -o, --output TEXT       Save to file instead of printing
  --help                  Show this message and exit.
```

#### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `PATH` | No | `domains/` | Path to a domain directory. Must exist on disk. |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--type` | Choice: `er`, `state`, `deps` | `er` | Diagram type to generate. |
| `-o`, `--output` | Text | `""` (print to terminal) | File path to save the Mermaid output. If empty, prints to terminal with syntax highlighting. |

#### Diagram Types

##### `er` -- Entity-Relationship Diagram

Generates a Mermaid `erDiagram` showing all entities, their fields, field types, and relationships (derived from `references` in field definitions).

##### `state` -- State Machine Diagram

Generates Mermaid `stateDiagram-v2` diagrams for each Workflow contract. Shows states, transitions, and the initial state. If there are multiple workflows, each gets its own diagram separated by blank lines.

##### `deps` -- Dependency Graph Diagram

Generates a Mermaid `graph TD` (top-down) diagram showing all contracts and their `requires` relationships. Different contract kinds use different node shapes:
- Entity: rectangle `[name]`
- Workflow: circle `((name))`
- Route: parallelogram `[/name/]`
- Page: asymmetric `>name]`

#### Examples

```bash
# ER diagram to terminal
spc factory visualize domains/library

# State machine diagram
spc factory visualize domains/library --type state

# Dependency graph saved to file
spc factory visualize domains/library --type deps -o docs/deps.mmd

# Via the REPL
> /visualize domains/library
> /visualize domains/library --type state
```

#### Related Commands

- `spc forge graph` -- Text-based dependency graph (Rich tree output)

---

### factory migrate

Import external schemas (OpenAPI, SQL DDL, Prisma) into Specora contracts via LLM.

```
Usage: spc factory migrate [OPTIONS] SOURCE

  Import external schemas into Specora contracts via LLM.

Options:
  -d, --domain TEXT               Target domain name  [required]
  --format [auto|openapi|sql|prisma]
                                  Source format
  --help                          Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `SOURCE` | Yes | Path to the source schema file. Must exist on disk. |

#### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `-d`, `--domain` | Text | Yes | N/A | Target domain name. Contracts will be written to `domains/<domain>/`. |
| `--format` | Choice: `auto`, `openapi`, `sql`, `prisma` | No | `auto` | Source format. `auto` detects from file extension and content. |

#### Format Auto-Detection

When `--format auto` (the default):

| File Extension | Detected Format |
|----------------|----------------|
| `.yaml`, `.yml` | `openapi` |
| `.sql` | `sql` |
| `.prisma` | `prisma` |

Content-based detection is used as fallback:
- Files containing `CREATE TABLE` (case-insensitive) are detected as `sql`
- Files containing `model ` and `@@` are detected as `prisma`
- All others default to `openapi`

#### Type Mapping

The LLM maps source types to Specora field types:

| SQL Types | Specora Type |
|-----------|-------------|
| `VARCHAR`, `TEXT`, `CHAR` | `string` |
| `INT`, `BIGINT`, `SERIAL` | `integer` |
| `DECIMAL`, `FLOAT`, `DOUBLE` | `number` |
| `BOOLEAN`, `BOOL` | `boolean` |
| `TIMESTAMP`, `TIMESTAMPTZ` | `datetime` |
| `DATE` | `date` |
| `UUID` | `uuid` |

Foreign keys are automatically converted to `references` with `graph_edge` labels.

#### Behavior

1. Reads the source file.
2. Auto-detects format (if `--format auto`).
3. Sends the content to the LLM for conversion.
4. Parses the LLM response into individual contract YAML documents.
5. Normalizes and validates each contract.
6. Skips contracts with validation errors (with warnings).
7. Previews all valid contracts with syntax highlighting.
8. Prompts for confirmation before writing.
9. Writes contracts to `domains/<domain>/entities/`.

#### Requires

- An LLM provider configured via environment variables.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Migration completed or user cancelled |
| 1 | LLM configuration error, LLM error, no contracts extracted, or all contracts failed validation |

#### Examples

```bash
# Migrate from an OpenAPI spec
spc factory migrate api-spec.yaml -d my_app

# Migrate from SQL DDL
spc factory migrate schema.sql -d my_app --format sql

# Migrate from Prisma schema
spc factory migrate schema.prisma -d my_app --format prisma

# Via the REPL
> /migrate schema.sql -d my_app
```

#### Related Commands

- `spc extract` -- Reverse-engineer from source code (not schema files)

---

## Healer Commands

The Healer is the self-healing pipeline. It detects contract validation errors, creates tickets, proposes fixes (via LLM), and applies them with your approval.

```
Usage: spc healer [OPTIONS] COMMAND [ARGS]...

  The Healer -- self-healing contract pipeline.

Options:
  --help  Show this message and exit.

Commands:
  approve  Approve a proposed fix and apply it.
  fix      Load contracts, validate, create tickets for errors, and process fixes.
  history  Show applied healer fixes from the diff store.
  reject   Reject a proposed fix.
  serve    Start the Healer HTTP service.
  show     Show detailed information about a ticket.
  status   Show queue statistics.
  tickets  List healer tickets with optional filters.
```

### Ticket Lifecycle

Healer tickets progress through these statuses:

```
queued -> analyzing -> proposed -> approved -> applied
                          |
                          +-------> rejected

(any stage) -> failed
```

| Status | Description |
|--------|-------------|
| `queued` | Ticket created, waiting for analysis |
| `analyzing` | Pipeline is analyzing the error |
| `proposed` | A fix has been proposed, awaiting human approval |
| `approved` | Fix approved by human, being applied |
| `applied` | Fix successfully applied to the contract file |
| `failed` | Processing failed at some stage |
| `rejected` | Human rejected the proposed fix |

### Storage

The Healer uses a SQLite database at `.forge/healer/healer.db`. It is created automatically on first use.

---

### healer fix

Scan contracts, validate, create tickets for errors, and process fixes.

```
Usage: spc healer fix [OPTIONS] [PATH]

  Load contracts, validate, create tickets for errors, and process fixes.

  Scans the contract directory, validates all contracts, creates healer
  tickets for any validation errors, and processes them through the pipeline.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `PATH` | No | `domains/` | Path to a domain directory. |

#### Behavior

1. Loads all contracts from the path.
2. Validates against meta-schemas.
3. If no errors: prints success message and exits.
4. For each validation error (severity = `error`): creates a healer ticket with source `VALIDATION`.
5. Processes all queued tickets through the pipeline (analyze, propose, apply).
6. Prints a summary: applied, proposed (awaiting approval), and failed counts.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Completed (may have proposed or failed tickets) |
| 1 | Could not load contracts |

#### Examples

```bash
# Fix all domains
spc healer fix

# Fix a specific domain
spc healer fix domains/library

# Via the REPL
> /heal
> /heal domains/library
```

#### Related Commands

- `spc healer tickets` -- View tickets awaiting approval
- `spc healer approve` -- Approve a proposed fix

---

### healer status

Show queue statistics.

```
Usage: spc healer status [OPTIONS]

  Show queue statistics.

Options:
  --output [text|json]  Output format
  --help                Show this message and exit.
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--output` | Choice: `text`, `json` | `text` | Output format. |

#### Text Output

Displays a Rich table with ticket counts per status:

```
        Healer Queue Status
┌───────────┬───────┐
│ Status    │ Count │
├───────────┼───────┤
│ queued    │     0 │
│ analyzing │     0 │
│ proposed  │     2 │
│ approved  │     0 │
│ applied   │     5 │
│ failed    │     1 │
│ rejected  │     0 │
├───────────┼───────┤
│ Total     │     8 │
└───────────┴───────┘
```

#### JSON Output Schema

```json
{
  "by_status": {
    "queued": 0,
    "proposed": 2,
    "applied": 5,
    "failed": 1
  },
  "total": 8
}
```

#### Examples

```bash
spc healer status
spc healer status --output json

# Via the REPL
> /status
```

---

### healer tickets

List healer tickets with optional filters.

```
Usage: spc healer tickets [OPTIONS]

  List healer tickets with optional filters.

Options:
  --status [queued|analyzing|proposed|approved|applied|failed|rejected]
                                  Filter by ticket status
  --priority [critical|high|medium|low]
                                  Filter by priority
  --output [text|json]            Output format
  --help                          Show this message and exit.
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--status` | Choice: `queued`, `analyzing`, `proposed`, `approved`, `applied`, `failed`, `rejected` | None (all) | Filter tickets by status. Case-insensitive. |
| `--priority` | Choice: `critical`, `high`, `medium`, `low` | None (all) | Filter tickets by priority. Case-insensitive. |
| `--output` | Choice: `text`, `json` | `text` | Output format. |

#### Text Output

Displays a Rich table with columns: ID (first 8 chars), Status, Priority, Tier, Contract FQN, and Error (truncated to 40 chars).

#### JSON Output Schema

```json
[
  {
    "id": "a1b2c3d4-...",
    "status": "proposed",
    "priority": "high",
    "tier": 1,
    "contract_fqn": "entity/library/book",
    "error": "Missing required field: spec.fields"
  }
]
```

#### Examples

```bash
# List all tickets
spc healer tickets

# List only proposed tickets
spc healer tickets --status proposed

# List high-priority tickets
spc healer tickets --priority high

# Combined filter
spc healer tickets --status proposed --priority critical

# JSON for scripting
spc healer tickets --output json

# Via the REPL
> /tickets
```

---

### healer show

Show detailed information about a ticket.

```
Usage: spc healer show [OPTIONS] TICKET_ID

  Show detailed information about a ticket.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `TICKET_ID` | Yes | Full UUID or unique prefix (minimum 4 characters) of the ticket. |

#### Short ID Resolution

You can use a prefix of the ticket ID instead of the full UUID. The system resolves it:
- If exactly one ticket matches the prefix, it is used.
- If multiple tickets match, an error is shown listing all ambiguous matches.
- If no ticket matches, an error is shown.

#### Output Fields

| Field | Description |
|-------|-------------|
| Status | Current ticket status |
| Priority | `critical`, `high`, `medium`, or `low` |
| Tier | Numeric tier level |
| Source | How the ticket was created (`validation`, etc.) |
| Contract | FQN of the affected contract |
| Error | Error type classification |
| Message | Full error message |
| Created | Creation timestamp (UTC) |
| Resolved | Resolution timestamp (if resolved) |
| Note | Resolution note (if any) |
| Context | Additional context (path, source file, etc.) |
| Proposal | Fix proposal details: method, confidence, explanation, change count |

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Ticket displayed |
| 1 | Ticket not found or ambiguous ID |

#### Examples

```bash
# Show by full ID
spc healer show a1b2c3d4-e5f6-7890-abcd-ef1234567890

# Show by prefix
spc healer show a1b2c3d4
```

---

### healer approve

Approve a proposed fix and apply it.

```
Usage: spc healer approve [OPTIONS] TICKET_ID

  Approve a proposed fix and apply it.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `TICKET_ID` | Yes | Full UUID or unique prefix of the ticket. |

#### Behavior

1. Resolves the ticket ID (supports prefix matching).
2. Checks that the ticket is in `proposed` status.
3. Applies the proposed fix to the contract file.
4. Updates the ticket status to `applied`.

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Fix approved and applied |
| 1 | Ticket not found, ticket not in `proposed` status, or application failed |

#### Examples

```bash
# Approve by prefix
spc healer approve a1b2c3d4

# Full workflow: fix -> review -> approve
spc healer fix domains/library
spc healer tickets --status proposed
spc healer show a1b2c3d4
spc healer approve a1b2c3d4
```

#### Related Commands

- `spc healer reject` -- Reject a proposed fix instead
- `spc healer tickets` -- List tickets to find IDs

---

### healer reject

Reject a proposed fix.

```
Usage: spc healer reject [OPTIONS] TICKET_ID

  Reject a proposed fix.

Options:
  -r, --reason TEXT  Reason for rejection
  --help             Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `TICKET_ID` | Yes | Full UUID or unique prefix of the ticket. |

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-r`, `--reason` | Text | `""` | Reason for rejecting the fix. Recorded on the ticket for future reference. |

#### Behavior

1. Resolves the ticket ID.
2. Checks that the ticket is in `proposed` status.
3. Updates the ticket status to `rejected`.
4. Records the rejection reason (if provided).

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Fix rejected |
| 1 | Ticket not found, or ticket not in `proposed` status |

#### Examples

```bash
# Reject without reason
spc healer reject a1b2c3d4

# Reject with reason
spc healer reject a1b2c3d4 -r "The proposed field name is wrong"
spc healer reject a1b2c3d4 --reason "Would break existing API clients"
```

---

### healer history

Show applied healer fixes from the diff store.

```
Usage: spc healer history [OPTIONS]

  Show applied healer fixes from the diff store.

Options:
  --help  Show this message and exit.
```

#### Behavior

Queries the diff store (`.forge/diffs/`) for all diffs with origin `HEALER` and displays them in a Rich table.

#### Output Columns

| Column | Description |
|--------|-------------|
| Date | When the fix was applied (YYYY-MM-DD HH:MM) |
| Contract | FQN of the modified contract |
| Reason | Why the fix was applied (truncated to 50 chars) |
| Changes | Number of individual field changes |

#### Examples

```bash
spc healer history

# Via the REPL
> /history
```

#### Related Commands

- `spc diff history` -- View all changes (not just healer) for a specific contract

---

### healer serve

Start the Healer HTTP service.

```
Usage: spc healer serve [OPTIONS]

  Start the Healer HTTP service.

Options:
  --port INTEGER  Port to serve on
  --host TEXT     Host to bind to
  --help          Show this message and exit.
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port` | Integer | `8083` | Port to bind the HTTP server to. |
| `--host` | Text | `0.0.0.0` | Host address to bind to. Use `0.0.0.0` for all interfaces or `127.0.0.1` for localhost only. |

#### Behavior

Starts a FastAPI/Uvicorn HTTP server exposing the Healer API. This enables external systems (CI pipelines, monitoring tools) to submit errors and query ticket status programmatically.

#### Requires

- The `healer` optional dependency group (`pip install -e ".[healer]"`).

#### Examples

```bash
# Start on default port (8083)
spc healer serve

# Custom port
spc healer serve --port 9000

# Localhost only
spc healer serve --host 127.0.0.1 --port 8083
```

---

## Extractor Commands

### extract

Reverse-engineer a codebase into Specora contracts.

```
Usage: spc extract [OPTIONS] PATH

  Reverse-engineer a codebase into Specora contracts.

  Analyzes Python and TypeScript source files, extracts entities, routes, and
  workflows, then emits contract YAML files.

Options:
  -d, --domain TEXT  Domain name (auto-inferred from directory name if omitted)
  -o, --output TEXT  Output base directory
  --help             Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `PATH` | Yes | Path to the source codebase directory. Must exist on disk. |

#### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `-d`, `--domain` | Text | No | Auto-inferred from directory name | Domain name for the generated contracts. Converted to `snake_case`. |
| `-o`, `--output` | Text | No | `domains/` | Base output directory. Contracts are written to `<output>/<domain>/`. |

#### Supported Languages

| Language | What is Extracted |
|----------|-------------------|
| Python | Pydantic models, SQLAlchemy models, dataclasses, FastAPI routes |
| TypeScript | TypeScript interfaces, type aliases |

#### Pipeline

The extractor runs a 4-pass pipeline:

1. **File scanning** -- Discover all `.py` and `.ts`/`.tsx` files
2. **Model extraction** -- Identify entities (classes, models, interfaces)
3. **Route extraction** -- Identify API endpoints
4. **Synthesis** -- Combine findings into a unified extraction report

#### Interactive Review

After extraction, each discovered entity is presented for review. You can accept or reject individual entities before contracts are written.

#### Generated Contracts

For each accepted entity:
- `entities/<name>.contract.yaml` -- Entity contract
- `routes/<name>s.contract.yaml` -- Route contract
- `pages/<name>s.contract.yaml` -- Page contract

#### Examples

```bash
# Extract from a Python project
spc extract ./my-flask-app

# Extract with explicit domain name
spc extract ./my-flask-app -d my_app

# Extract to custom output directory
spc extract ./my-flask-app -d my_app -o output/

# Via the REPL
> /extract ./my-flask-app
> /extract ./my-flask-app --domain my_app
```

#### Related Commands

- `spc factory migrate` -- Import from schema files (OpenAPI, SQL, Prisma) rather than source code

---

## Diff Commands

Contract diff tracking. Every mutation to a contract file can be tracked with a structured diff that records what changed, who changed it, and why.

```
Usage: spc diff [OPTIONS] COMMAND [ARGS]...

  Contract diff tracking.

Options:
  --help  Show this message and exit.

Commands:
  history  Show the change history for a contract.
  show     Show details of a specific diff.
```

### Storage

Diffs are stored as JSON files in `.forge/diffs/`, organized by contract FQN.

---

### diff history

Show the change history for a specific contract.

```
Usage: spc diff history [OPTIONS] CONTRACT_FQN

  Show the change history for a contract.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `CONTRACT_FQN` | Yes | Fully qualified name of the contract (e.g., `entity/library/book`). |

#### Output Columns

| Column | Description |
|--------|-------------|
| Date | When the change was made (YYYY-MM-DD HH:MM) |
| Origin | Who made the change: `human`, `healer`, `advisor`, or `factory` (with optional detail) |
| Reason | Why the change was made (truncated to 60 chars) |
| Changes | Number of individual field-level changes |

Diffs are displayed in reverse chronological order (newest first).

#### Examples

```bash
# View history for an entity
spc diff history entity/library/book

# View history for a workflow
spc diff history workflow/library/book_lifecycle
```

#### Related Commands

- `spc diff show` -- View details of a specific diff
- `spc healer history` -- View only healer-originated diffs

---

### diff show

Show details of a specific diff.

```
Usage: spc diff show [OPTIONS] DIFF_ID

  Show details of a specific diff.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `DIFF_ID` | Yes | UUID of the diff to display. |

#### Output

Displays full diff details:

| Field | Description |
|-------|-------------|
| Diff ID | Full UUID |
| Contract | FQN of the affected contract |
| Date | Timestamp (UTC) |
| Origin | Who made the change (with detail) |
| Reason | Why the change was made |

Then lists all individual changes with type indicators:
- `+` (green) -- Added field/value
- `-` (red) -- Removed field/value
- `~` (yellow) -- Modified field/value (shows old -> new)

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Diff displayed |
| 1 | Diff not found |

#### Examples

```bash
# View a specific diff
spc diff show a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

---

## Utility Commands

### init

Scaffold a new domain with starter contracts.

```
Usage: spc init [OPTIONS] DOMAIN

  Scaffold a new domain with starter contracts.

  Creates the directory structure and a starter entity contract.

Options:
  --help  Show this message and exit.
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `DOMAIN` | Yes | Name of the domain to create. Used as the directory name under `domains/`. |

#### Created Structure

```
domains/<domain>/
  entities/
    example.contract.yaml    # Starter entity with name, description, active fields
  workflows/                 # Empty, ready for workflow contracts
  pages/                     # Empty, ready for page contracts
  routes/                    # Empty, ready for route contracts
  agents/                    # Empty, ready for agent contracts
```

#### Starter Entity

The generated `example.contract.yaml` contains:

```yaml
apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: example
  domain: <domain>
  description: "A starter entity -- replace with your own"
  tags: [starter]

requires:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable

spec:
  fields:
    name:
      type: string
      required: true
      description: "The name of this record"
      constraints:
        maxLength: 200
    description:
      type: text
      description: "Detailed description"
    active:
      type: boolean
      default: true
      description: "Whether this record is active"
```

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Domain scaffolded successfully |
| 1 | Domain directory already exists |

#### Examples

```bash
# Create a new domain
spc init healthcare

# Then edit the starter entity
# $EDITOR domains/healthcare/entities/example.contract.yaml

# Validate
spc forge validate domains/healthcare

# Generate code
spc forge generate domains/healthcare
```

#### Related Commands

- `spc factory new` -- Create a domain via LLM interview (richer output)

---

## Environment Variables

### LLM Provider Configuration

These variables configure the LLM engine used by Factory, Healer, and Extractor commands. They are probed in priority order -- the first match wins.

| Variable | Priority | Default Model | Description |
|----------|----------|---------------|-------------|
| `SPECORA_AI_MODEL` | 1 (highest) | Explicit model | Override model selection. Must be a model ID known to the registry. Requires the corresponding provider API key. |
| `ANTHROPIC_API_KEY` | 2 | `claude-sonnet-4-6` | Anthropic API key. Selects Claude as the provider. |
| `OPENAI_API_KEY` | 3 | `gpt-4o` | OpenAI API key. Selects GPT-4o. |
| `XAI_API_KEY` | 4 | `grok-3-mini` | xAI API key. Uses the xAI base URL (`https://api.x.ai/v1`). |
| `OLLAMA_BASE_URL` | 5 (lowest) | `llama3.3:70b` | Ollama base URL for local models (e.g., `http://localhost:11434`). No API key needed. |

When `SPECORA_AI_MODEL` is set, the provider is determined from the model's registry entry. The corresponding API key variable must also be set.

When `XAI_API_KEY` is set without `OPENAI_API_KEY`, the xAI base URL is automatically configured.

### Other Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EDITOR` | N/A | Editor used by `factory new` for contract preview. Falls back to `VISUAL`. |
| `VISUAL` | N/A | Fallback editor if `EDITOR` is not set. |
| `SPECORA_HEALER_WEBHOOK_URL` | N/A | Webhook URL for healer notifications. |

### .env File Support

The CLI loads `.env` files from the current directory using `python-dotenv` with `override=True`. This means `.env` values override system-level environment variables.

---

## Configuration

### Directory Hierarchy

| Path | Purpose | Created By |
|------|---------|------------|
| `~/.specora/` | User-level config directory | REPL (for command history) |
| `~/.specora/history` | REPL command history | REPL (auto-created) |
| `.forge/` | Project-level Forge data | Forge commands |
| `.forge/diffs/` | Contract diff storage (JSON) | `factory refine`, Healer |
| `.forge/healer/` | Healer data directory | Healer commands |
| `.forge/healer/healer.db` | Healer ticket database (SQLite) | Healer commands |
| `.specora/session/` | Factory session persistence | `factory new` |
| `domains/` | Contract source directory | `init`, `factory new`, `extract` |
| `runtime/` | Generated code output | `forge generate` |

### Standard Library

The standard library at `spec/stdlib/` provides reusable contracts:

| Path | Contents |
|------|----------|
| `spec/stdlib/mixins/` | `timestamped`, `identifiable`, `auditable`, `taggable`, `commentable`, `soft_deletable` |
| `spec/stdlib/workflows/` | `crud_lifecycle`, `approval`, `ticket` |

These are automatically discovered and available via `requires` references (e.g., `mixin/stdlib/timestamped`).

---

## Workflows

### Creating a New Domain from Scratch

```bash
# Option A: Quick scaffold (no LLM)
spc init my_app
# Edit domains/my_app/entities/*.contract.yaml manually

# Option B: LLM-guided interview
export ANTHROPIC_API_KEY="sk-ant-..."
spc factory new
# Follow the interactive interview

# Validate
spc forge validate domains/my_app

# Generate code
spc forge generate domains/my_app

# Inspect output
ls runtime/
```

### Extracting Contracts from an Existing Codebase

```bash
# Point the extractor at your source code
spc extract ./my-existing-app -d my_app

# Review and accept/reject extracted entities interactively

# Validate the generated contracts
spc forge validate domains/my_app

# Fix any issues
spc healer fix domains/my_app

# Generate fresh code
spc forge generate domains/my_app
```

### Migrating from an External Schema

```bash
# From OpenAPI
spc factory migrate api-spec.yaml -d my_app

# From SQL DDL
spc factory migrate schema.sql -d my_app --format sql

# From Prisma
spc factory migrate schema.prisma -d my_app --format prisma

# Validate the results
spc forge validate domains/my_app
```

### Fixing Validation Errors

```bash
# See what's wrong
spc forge validate domains/my_app

# Auto-fix with the Healer
spc healer fix domains/my_app

# Check for proposed fixes
spc healer tickets --status proposed

# Review a proposed fix
spc healer show a1b2c3d4

# Approve or reject
spc healer approve a1b2c3d4
spc healer reject a1b2c3d4 -r "Wrong approach"
```

### Modifying a Contract

```bash
# Understand the contract first
spc factory explain domains/library/entities/book.contract.yaml

# Make a change via natural language
spc factory refine domains/library/entities/book.contract.yaml "add a page_count integer field"

# Verify the change
spc forge validate domains/library

# View the diff history
spc diff history entity/library/book

# Regenerate code
spc forge generate domains/library
```

### Generating Code

```bash
# All targets (TypeScript + FastAPI + PostgreSQL)
spc forge generate domains/library

# Specific targets
spc forge generate domains/library -t typescript
spc forge generate domains/library -t postgres
spc forge generate domains/library -t typescript -t postgres

# Custom output directory
spc forge generate domains/library -o output/
```

### Monitoring and Self-Healing

```bash
# Start the Healer HTTP service (for CI/runtime integration)
spc healer serve --port 8083

# Check queue status
spc healer status

# View all tickets
spc healer tickets

# View healer fix history
spc healer history

# View all change history for a specific contract
spc diff history entity/library/book
```

### Interactive Development (REPL)

```bash
# Launch the REPL
spc

# Quick validation cycle
> /validate
> /compile
> /generate

# LLM-powered authoring
> /new
> /chat -d library
> /explain domains/library/entities/book.contract.yaml
> /refine domains/library/entities/book.contract.yaml add an isbn field

# Visualize your domain
> /visualize domains/library
> /visualize domains/library --type state
> /graph

# Self-healing
> /heal
> /status
> /tickets

# Shell escape
> ! ls runtime/
> ! cat runtime/database/schema.sql
```

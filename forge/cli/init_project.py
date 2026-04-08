"""specora-init — scaffold a new standalone Specora project.

Creates a project directory anywhere on the filesystem with the
complete structure needed to start building with contracts.

Usage:
    specora-init helpdesk
    specora-init my_app --path C:/projects/
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()

GITIGNORE = """# Generated code (regeneratable from contracts)
runtime/
!runtime/.gitkeep

# Environment
.env
.venv/
__pycache__/

# Healer state
.forge/healer/

# OS
.DS_Store
Thumbs.db
"""

README_TEMPLATE = """# {name}

Built with [Specora Core](https://github.com/syndicalt/specora-core) — Contract-Driven Development.

## Quick Start

```bash
# Generate the app
spc forge generate domains/{domain} --target fastapi-prod --target postgres --target docker

# Boot it
docker compose up -d

# API docs
open http://localhost:8000/docs
```

## How to Work

Talk to your LLM. The CLAUDE.md file teaches it everything about this project.

- **Add an entity**: ask your LLM to create a contract in `domains/{domain}/entities/`
- **Add a workflow**: `domains/{domain}/workflows/`
- **Validate**: the LLM calls `validate_all()` directly
- **Generate**: the LLM calls the generators directly
- **Deploy**: `docker compose up -d --build`

## Project Structure

```
domains/          <- Your contracts (source of truth)
runtime/          <- Generated code (disposable)
.env.example      <- Environment variables
docker-compose.yml <- Generated deployment
CLAUDE.md         <- LLM operating manual
```

## The Rule

**Contracts are the product. Code is derived. Delete runtime/ and regenerate.**
"""


@click.command("init-project")
@click.argument("name")
@click.option("--path", "-p", default=".", help="Parent directory for the project")
def init_project(name: str, path: str) -> None:
    """Scaffold a new standalone Specora project."""
    from forge.normalize import normalize_name

    domain = normalize_name(name)
    project_dir = Path(path).resolve() / name

    if project_dir.exists():
        console.print(f"[red]Directory already exists:[/red] {project_dir}")
        sys.exit(1)

    # Create structure
    project_dir.mkdir(parents=True)
    for subdir in ["entities", "workflows", "routes", "pages", "agents"]:
        (project_dir / "domains" / domain / subdir).mkdir(parents=True)

    (project_dir / "runtime").mkdir()
    (project_dir / "runtime" / ".gitkeep").touch()
    (project_dir / ".forge").mkdir()

    # .gitignore
    (project_dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")

    # README
    (project_dir / "README.md").write_text(
        README_TEMPLATE.format(name=name, domain=domain), encoding="utf-8"
    )

    # .env.example — generate a comprehensive one
    from forge.ir.model import DomainIR
    from forge.targets.fastapi_prod.gen_docker import _generate_env_example
    env_file = _generate_env_example(DomainIR(domain=domain), has_auth=False)
    (project_dir / ".env.example").write_text(env_file.content, encoding="utf-8")

    # Copy .env.example to .env
    shutil.copy(project_dir / ".env.example", project_dir / ".env")

    # CLAUDE.md — copy from specora-core if available
    specora_core_claude = Path(__file__).resolve().parent.parent.parent / "CLAUDE.md"
    if specora_core_claude.exists():
        shutil.copy(specora_core_claude, project_dir / "CLAUDE.md")
    else:
        (project_dir / "CLAUDE.md").write_text(
            "# See specora-core CLAUDE.md for the complete LLM operating manual.\n",
            encoding="utf-8",
        )

    # Starter entity
    starter = f"""apiVersion: specora.dev/v1
kind: Entity
metadata:
  name: example
  domain: {domain}
  description: "A starter entity — replace with your own"
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
  mixins:
    - mixin/stdlib/timestamped
    - mixin/stdlib/identifiable
"""
    (project_dir / "domains" / domain / "entities" / "example.contract.yaml").write_text(
        starter, encoding="utf-8"
    )

    # Print result
    console.print()
    console.print(f"[bold green]Project created:[/bold green] {project_dir}")
    console.print()
    console.print(f"  [cyan]domains/{domain}/[/cyan]  Your contracts go here")
    console.print(f"  [cyan]runtime/[/cyan]           Generated code (disposable)")
    console.print(f"  [cyan]CLAUDE.md[/cyan]          LLM operating manual")
    console.print(f"  [cyan].env[/cyan]               Environment configuration")
    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  1. cd {name}")
    console.print(f"  2. Edit .env (add your LLM API key)")
    console.print(f"  3. Open your LLM and start building")
    console.print(f"     Or: spc forge validate domains/{domain}")
    console.print()

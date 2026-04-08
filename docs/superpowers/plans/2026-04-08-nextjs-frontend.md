# Next.js Frontend Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a complete Next.js 15 frontend with shadcn/ui from the same contracts that generate the backend — table, kanban, forms, detail views, filters, sidebar, dashboard, API client.

**Architecture:** New `nextjs` generator target with 6 sub-generators: scaffold (project config), API client (from RouteIR), components (DataTable, KanbanBoard, EntityForm, etc.), pages (from PageIR), layout (sidebar from all PageIRs), and Docker (Dockerfile.frontend). Each sub-generator produces `GeneratedFile` objects containing TSX/TS/JSON strings.

**Tech Stack:** Python generators emitting TypeScript/TSX, Next.js 15 App Router, shadcn/ui, Tailwind CSS, Lucide React.

**Spec:** `docs/superpowers/specs/2026-04-08-nextjs-frontend-design.md`
**Issue:** syndicalt/specora-core#13

---

## File Map

### Generator files (Python — in specora-core)

| File | Responsibility |
|------|---------------|
| `forge/targets/nextjs/__init__.py` | Package init |
| `forge/targets/nextjs/gen_scaffold.py` | package.json, next.config.js, tailwind.config.js, postcss.config.js, tsconfig.json, utils.ts |
| `forge/targets/nextjs/gen_api_client.py` | lib/api.ts from RouteIR |
| `forge/targets/nextjs/gen_components.py` | Reusable components: DataTable, KanbanBoard, EntityForm, FilterSidebar, DetailView, shadcn ui/ primitives |
| `forge/targets/nextjs/gen_pages.py` | App router pages: list, detail, create per PageIR |
| `forge/targets/nextjs/gen_layout.py` | Root layout.tsx + AppSidebar + Dashboard page |
| `forge/targets/nextjs/gen_docker.py` | Dockerfile.frontend |
| `forge/targets/nextjs/generator.py` | NextJSGenerator — orchestrates all sub-generators |
| `tests/test_targets/test_nextjs.py` | Tests for the generator |

### Generated files (TypeScript/TSX — output)

All paths relative to `frontend/`:
- `package.json`, `next.config.js`, `tailwind.config.js`, `postcss.config.js`, `tsconfig.json`
- `src/lib/api.ts`, `src/lib/types.ts` (from existing TS generator), `src/lib/utils.ts`
- `src/components/ui/*.tsx` (shadcn primitives)
- `src/components/DataTable.tsx`, `KanbanBoard.tsx`, `EntityForm.tsx`, `FilterSidebar.tsx`, `DetailView.tsx`, `AppSidebar.tsx`
- `src/app/layout.tsx`, `src/app/page.tsx`
- `src/app/{route}/page.tsx`, `src/app/{route}/[id]/page.tsx`, `src/app/{route}/new/page.tsx`
- `Dockerfile.frontend`

---

### Task 1: Project Scaffold Generator

**Files:**
- Create: `forge/targets/nextjs/__init__.py`
- Create: `forge/targets/nextjs/gen_scaffold.py`
- Create: `tests/test_targets/test_nextjs.py`

This generates the project configuration files that don't change per domain.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_targets/test_nextjs.py
"""Tests for the Next.js frontend generator."""
import json

import pytest

from forge.ir.model import DomainIR, EntityIR, FieldIR, PageIR, RouteIR, EndpointIR


@pytest.fixture
def helpdesk_ir() -> DomainIR:
    """A minimal helpdesk IR for testing frontend generation."""
    return DomainIR(
        domain="helpdesk",
        entities=[
            EntityIR(
                fqn="entity/helpdesk/ticket",
                name="ticket",
                domain="helpdesk",
                table_name="tickets",
                icon="ticket",
                fields=[
                    FieldIR(name="subject", type="string", required=True),
                    FieldIR(name="priority", type="string", enum_values=["critical", "high", "medium", "low"]),
                    FieldIR(name="id", type="uuid", computed="uuid"),
                    FieldIR(name="created_at", type="datetime", computed="now"),
                ],
            ),
        ],
        pages=[
            PageIR(
                fqn="page/helpdesk/tickets",
                name="tickets",
                domain="helpdesk",
                route="/tickets",
                title="Support Tickets",
                entity_fqn="entity/helpdesk/ticket",
                data_sources=[{"endpoint": "/tickets", "alias": "tickets"}],
                views=[
                    {"type": "table", "default": True, "columns": ["subject", "priority"]},
                    {"type": "kanban", "card_fields": ["subject", "priority"]},
                ],
            ),
        ],
        routes=[
            RouteIR(
                fqn="route/helpdesk/tickets",
                name="tickets",
                domain="helpdesk",
                entity_fqn="entity/helpdesk/ticket",
                base_path="/tickets",
                endpoints=[
                    EndpointIR(method="GET", path="/", summary="List tickets"),
                    EndpointIR(method="POST", path="/", summary="Create ticket", response_status=201),
                    EndpointIR(method="GET", path="/{id}", summary="Get ticket"),
                    EndpointIR(method="PATCH", path="/{id}", summary="Update ticket"),
                    EndpointIR(method="DELETE", path="/{id}", summary="Delete ticket", response_status=204),
                ],
            ),
        ],
    )


class TestGenScaffold:

    def test_generates_package_json(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold
        files = generate_scaffold(helpdesk_ir)
        pkg = next(f for f in files if f.path == "frontend/package.json")
        data = json.loads(pkg.content)
        assert data["name"] == "helpdesk-frontend"
        assert "next" in data["dependencies"]
        assert "react" in data["dependencies"]
        assert "tailwindcss" in data["devDependencies"]

    def test_generates_tailwind_config(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold
        files = generate_scaffold(helpdesk_ir)
        tw = next(f for f in files if f.path == "frontend/tailwind.config.js")
        assert "content" in tw.content
        assert "./src/" in tw.content

    def test_generates_utils(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold
        files = generate_scaffold(helpdesk_ir)
        utils = next(f for f in files if f.path == "frontend/src/lib/utils.ts")
        assert "cn(" in utils.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_targets/test_nextjs.py -v`

- [ ] **Step 3: Implement scaffold generator**

```python
# forge/targets/nextjs/__init__.py
# (empty)
```

```python
# forge/targets/nextjs/gen_scaffold.py
"""Generate Next.js project scaffold — package.json, configs, utils."""
from __future__ import annotations

import json

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile


def generate_scaffold(ir: DomainIR) -> list[GeneratedFile]:
    """Generate project configuration files."""
    return [
        _package_json(ir),
        _next_config(ir),
        _tailwind_config(ir),
        _postcss_config(ir),
        _tsconfig(ir),
        _utils(ir),
    ]


def _package_json(ir: DomainIR) -> GeneratedFile:
    data = {
        "name": f"{ir.domain}-frontend",
        "version": "0.1.0",
        "private": True,
        "scripts": {
            "dev": "next dev",
            "build": "next build",
            "start": "next start",
            "lint": "next lint",
        },
        "dependencies": {
            "next": "^15.0.0",
            "react": "^18.3.0",
            "react-dom": "^18.3.0",
            "lucide-react": "^0.400.0",
            "clsx": "^2.1.0",
            "tailwind-merge": "^2.3.0",
            "class-variance-authority": "^0.7.0",
        },
        "devDependencies": {
            "typescript": "^5.6.0",
            "@types/react": "^18.3.0",
            "@types/node": "^22.0.0",
            "tailwindcss": "^3.4.0",
            "postcss": "^8.4.0",
            "autoprefixer": "^10.4.0",
        },
    }
    return GeneratedFile(
        path="frontend/package.json",
        content=json.dumps(data, indent=2),
        provenance=f"domain/{ir.domain}",
    )


def _next_config(ir: DomainIR) -> GeneratedFile:
    content = """/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
};

module.exports = nextConfig;
"""
    return GeneratedFile(path="frontend/next.config.js", content=content, provenance=f"domain/{ir.domain}")


def _tailwind_config(ir: DomainIR) -> GeneratedFile:
    content = """/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
"""
    return GeneratedFile(path="frontend/tailwind.config.js", content=content, provenance=f"domain/{ir.domain}")


def _postcss_config(ir: DomainIR) -> GeneratedFile:
    content = """module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""
    return GeneratedFile(path="frontend/postcss.config.js", content=content, provenance=f"domain/{ir.domain}")


def _tsconfig(ir: DomainIR) -> GeneratedFile:
    data = {
        "compilerOptions": {
            "target": "ES2017",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./src/*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
        "exclude": ["node_modules"],
    }
    return GeneratedFile(
        path="frontend/tsconfig.json",
        content=json.dumps(data, indent=2),
        provenance=f"domain/{ir.domain}",
    )


def _utils(ir: DomainIR) -> GeneratedFile:
    content = """import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: string | null | undefined): string {
  if (!date) return "—";
  return new Date(date).toLocaleDateString();
}

export function formatDateTime(date: string | null | undefined): string {
  if (!date) return "—";
  return new Date(date).toLocaleString();
}

export function truncate(str: string, length: number = 50): string {
  if (str.length <= length) return str;
  return str.slice(0, length) + "…";
}
"""
    return GeneratedFile(path="frontend/src/lib/utils.ts", content=content, provenance=f"domain/{ir.domain}")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_targets/test_nextjs.py -v`

- [ ] **Step 5: Commit**

```bash
git add forge/targets/nextjs/ tests/test_targets/test_nextjs.py
git commit -m "feat(#13/T1): Next.js scaffold generator — package.json, configs, utils"
```

---

### Task 2: API Client Generator

**Files:**
- Create: `forge/targets/nextjs/gen_api_client.py`
- Modify: `tests/test_targets/test_nextjs.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_targets/test_nextjs.py`:

```python
class TestGenAPIClient:

    def test_generates_api_client(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_api_client import generate_api_client
        file = generate_api_client(helpdesk_ir)
        assert file.path == "frontend/src/lib/api.ts"
        assert "NEXT_PUBLIC_API_URL" in file.content
        assert "export const tickets" in file.content
        assert "list:" in file.content
        assert "create:" in file.content
        assert "get:" in file.content
        assert "update:" in file.content
        assert 'method: "DELETE"' in file.content

    def test_api_client_uses_base_path(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_api_client import generate_api_client
        file = generate_api_client(helpdesk_ir)
        assert "/tickets/" in file.content
```

- [ ] **Step 2: Implement API client generator**

```python
# forge/targets/nextjs/gen_api_client.py
"""Generate TypeScript API client from RouteIR."""
from __future__ import annotations

from forge.ir.model import DomainIR, RouteIR, EndpointIR
from forge.targets.base import GeneratedFile, provenance_header


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def generate_api_client(ir: DomainIR) -> GeneratedFile:
    """Generate frontend/src/lib/api.ts with a typed fetch client."""
    lines = [
        '// @generated — API client from route contracts',
        '',
        'const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";',
        '',
        'async function _fetch(path: string, opts?: RequestInit) {',
        '  const res = await fetch(`${API}${path}`, {',
        '    ...opts,',
        '    headers: { "Content-Type": "application/json", ...opts?.headers },',
        '  });',
        '  if (res.status === 204) return null;',
        '  if (!res.ok) throw new Error(`API error: ${res.status}`);',
        '  return res.json();',
        '}',
        '',
    ]

    for route in ir.routes:
        entity_name = route.entity_fqn.split("/")[-1] if route.entity_fqn else route.name
        base = route.base_path or f"/{entity_name}s"

        lines.append(f"export const {route.name} = {{")

        for ep in route.endpoints:
            fn = _endpoint_to_function(ep, entity_name, base)
            if fn:
                lines.append(f"  {fn}")

        lines.append("};")
        lines.append("")

    return GeneratedFile(
        path="frontend/src/lib/api.ts",
        content="\n".join(lines),
        provenance=", ".join(r.fqn for r in ir.routes),
    )


def _endpoint_to_function(ep: EndpointIR, entity_name: str, base: str) -> str:
    method = ep.method.upper()
    path = ep.path

    if method == "GET" and path == "/":
        return f'list: (limit = 100, offset = 0) => _fetch(`{base}/?limit=${{limit}}&offset=${{offset}}`),'
    if method == "GET" and "{id}" in path:
        return f'get: (id: string) => _fetch(`{base}/${{id}}`),'
    if method == "POST" and path == "/":
        return f'create: (data: any) => _fetch(`{base}/`, {{ method: "POST", body: JSON.stringify(data) }}),'
    if method == "PATCH" and "{id}" in path:
        return f'update: (id: string, data: any) => _fetch(`{base}/${{id}}`, {{ method: "PATCH", body: JSON.stringify(data) }}),'
    if method == "DELETE" and "{id}" in path:
        return f'delete: (id: string) => _fetch(`{base}/${{id}}`, {{ method: "DELETE" }}),'
    if method == "PUT" and "state" in path:
        return f'transition: (id: string, state: string) => _fetch(`{base}/${{id}}/state`, {{ method: "PUT", body: JSON.stringify({{ state }}) }}),'

    return ""
```

- [ ] **Step 3: Run tests + commit**

```bash
python -m pytest tests/test_targets/test_nextjs.py -v
git add forge/targets/nextjs/gen_api_client.py tests/test_targets/test_nextjs.py
git commit -m "feat(#13/T2): API client generator — typed fetch functions from RouteIR"
```

---

### Task 3: Reusable Components Generator

**Files:**
- Create: `forge/targets/nextjs/gen_components.py`

This generates the reusable components: DataTable, KanbanBoard, EntityForm, FilterSidebar, DetailView, and minimal shadcn ui primitives.

- [ ] **Step 1: Implement components generator**

```python
# forge/targets/nextjs/gen_components.py
"""Generate reusable React components for the frontend."""
from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, FieldIR, PageIR, StateMachineIR
from forge.targets.base import GeneratedFile


def generate_components(ir: DomainIR) -> list[GeneratedFile]:
    """Generate all reusable components."""
    files = [
        _generate_shadcn_button(),
        _generate_shadcn_input(),
        _generate_shadcn_badge(),
        _generate_shadcn_card(),
        _generate_shadcn_select(),
        _generate_shadcn_table(),
        _generate_globals_css(),
    ]

    # Entity-aware components — one per entity that has a page
    entity_map = {e.fqn: e for e in ir.entities}
    for page in ir.pages:
        entity = entity_map.get(page.entity_fqn)
        if entity:
            files.append(_generate_data_table(entity, page))
            files.append(_generate_entity_form(entity))
            files.append(_generate_detail_view(entity))
            if entity.state_machine:
                files.append(_generate_kanban_board(entity, page))

    files.append(_generate_app_sidebar(ir))

    return files


def _to_pascal(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


# ── shadcn primitives (minimal versions) ─────────────────────────────

def _generate_shadcn_button() -> GeneratedFile:
    content = '''"use client";
import { cn } from "@/lib/utils";
import { forwardRef, type ButtonHTMLAttributes } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "destructive" | "outline" | "ghost";
  size?: "default" | "sm" | "lg";
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => {
    const variants: Record<string, string> = {
      default: "bg-blue-600 text-white hover:bg-blue-700",
      destructive: "bg-red-600 text-white hover:bg-red-700",
      outline: "border border-gray-300 bg-white hover:bg-gray-50",
      ghost: "hover:bg-gray-100",
    };
    const sizes: Record<string, string> = {
      default: "h-10 px-4 py-2",
      sm: "h-8 px-3 text-sm",
      lg: "h-12 px-6",
    };
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center rounded-md font-medium transition-colors focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50",
          variants[variant],
          sizes[size],
          className
        )}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";
export { Button };
'''
    return GeneratedFile(path="frontend/src/components/ui/button.tsx", content=content, provenance="shadcn/ui")


def _generate_shadcn_input() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";
import { forwardRef, type InputHTMLAttributes } from "react";

const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";
export { Input };
'''
    return GeneratedFile(path="frontend/src/components/ui/input.tsx", content=content, provenance="shadcn/ui")


def _generate_shadcn_badge() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";

const colorMap: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  high: "bg-orange-100 text-orange-800",
  medium: "bg-yellow-100 text-yellow-800",
  low: "bg-green-100 text-green-800",
  open: "bg-blue-100 text-blue-800",
  hold: "bg-yellow-100 text-yellow-800",
  closed: "bg-gray-100 text-gray-800",
  default: "bg-gray-100 text-gray-800",
};

export function Badge({ value, className }: { value: string; className?: string }) {
  const color = colorMap[value] || colorMap.default;
  return (
    <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", color, className)}>
      {value}
    </span>
  );
}
'''
    return GeneratedFile(path="frontend/src/components/ui/badge.tsx", content=content, provenance="shadcn/ui")


def _generate_shadcn_card() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";

export function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("rounded-lg border bg-white p-6 shadow-sm", className)}>{children}</div>;
}

export function CardHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("mb-4", className)}>{children}</div>;
}

export function CardTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return <h3 className={cn("text-lg font-semibold", className)}>{children}</h3>;
}

export function CardContent({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("", className)}>{children}</div>;
}
'''
    return GeneratedFile(path="frontend/src/components/ui/card.tsx", content=content, provenance="shadcn/ui")


def _generate_shadcn_select() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";
import { forwardRef, type SelectHTMLAttributes } from "react";

const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500",
        className
      )}
      {...props}
    >
      {children}
    </select>
  )
);
Select.displayName = "Select";
export { Select };
'''
    return GeneratedFile(path="frontend/src/components/ui/select.tsx", content=content, provenance="shadcn/ui")


def _generate_shadcn_table() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";

export function Table({ children, className }: { children: React.ReactNode; className?: string }) {
  return <table className={cn("w-full caption-bottom text-sm", className)}>{children}</table>;
}
export function TableHeader({ children }: { children: React.ReactNode }) {
  return <thead className="border-b bg-gray-50">{children}</thead>;
}
export function TableBody({ children }: { children: React.ReactNode }) {
  return <tbody className="divide-y">{children}</tbody>;
}
export function TableRow({ children, className, onClick }: { children: React.ReactNode; className?: string; onClick?: () => void }) {
  return <tr className={cn("hover:bg-gray-50 cursor-pointer", className)} onClick={onClick}>{children}</tr>;
}
export function TableHead({ children, className }: { children: React.ReactNode; className?: string }) {
  return <th className={cn("h-12 px-4 text-left font-medium text-gray-500", className)}>{children}</th>;
}
export function TableCell({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn("px-4 py-3", className)}>{children}</td>;
}
'''
    return GeneratedFile(path="frontend/src/components/ui/table.tsx", content=content, provenance="shadcn/ui")


def _generate_globals_css() -> GeneratedFile:
    content = """@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
"""
    return GeneratedFile(path="frontend/src/app/globals.css", content=content, provenance="shadcn/ui")


# ── Entity-aware components ──────────────────────────────────────────

def _generate_data_table(entity: EntityIR, page: PageIR) -> GeneratedFile:
    cls = _to_pascal(entity.name)
    table_view = next((v for v in page.views if v.get("type") == "table"), None)
    columns = table_view.get("columns", []) if table_view else [f.name for f in entity.fields[:6]]

    col_headers = "\n".join(f'            <TableHead>{col.replace("_", " ").title()}</TableHead>' for col in columns)
    col_cells = []
    for col in columns:
        field = next((f for f in entity.fields if f.name == col), None)
        if field and field.enum_values:
            col_cells.append(f'            <TableCell><Badge value={{String(item.{col} || "")}} /></TableCell>')
        else:
            col_cells.append(f'            <TableCell>{{String(item.{col} ?? "—")}}</TableCell>')
    col_cells_str = "\n".join(col_cells)

    content = f'''"use client";
import {{ useRouter }} from "next/navigation";
import {{ Table, TableHeader, TableBody, TableRow, TableHead, TableCell }} from "@/components/ui/table";
import {{ Badge }} from "@/components/ui/badge";

interface {cls}TableProps {{
  items: any[];
  basePath: string;
}}

export function {cls}Table({{ items, basePath }}: {cls}TableProps) {{
  const router = useRouter();

  return (
    <Table>
      <TableHeader>
        <TableRow>
{col_headers}
            <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {{items.map((item: any) => (
          <TableRow key={{item.id}} onClick={{() => router.push(`${{basePath}}/${{item.id}}`)}}>
{col_cells_str}
            <TableCell>
              <button className="text-sm text-red-600 hover:underline" onClick={{(e) => {{ e.stopPropagation(); }}}}>
                Delete
              </button>
            </TableCell>
          </TableRow>
        ))}}
      </TableBody>
    </Table>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/components/{cls}Table.tsx",
        content=content,
        provenance=page.fqn,
    )


def _generate_entity_form(entity: EntityIR) -> GeneratedFile:
    cls = _to_pascal(entity.name)
    form_fields = [f for f in entity.fields if not f.computed and not f.immutable]

    field_inputs = []
    for f in form_fields:
        label = f.name.replace("_", " ").title()
        required = ' required' if f.required else ''

        if f.enum_values:
            options = "\n".join(f'            <option value="{v}">{v}</option>' for v in f.enum_values)
            field_inputs.append(f'''        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{label}{" *" if f.required else ""}</label>
          <select name="{f.name}" defaultValue={{data?.{f.name} || ""}}{required}
            className="flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm">
            <option value="">Select...</option>
{options}
          </select>
        </div>''')
        elif f.type == "text":
            field_inputs.append(f'''        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{label}{" *" if f.required else ""}</label>
          <textarea name="{f.name}" defaultValue={{data?.{f.name} || ""}}{required}
            className="flex w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm min-h-[100px]" />
        </div>''')
        elif f.type == "boolean":
            field_inputs.append(f'''        <div className="flex items-center gap-2">
          <input type="checkbox" name="{f.name}" defaultChecked={{data?.{f.name}}} className="h-4 w-4" />
          <label className="text-sm font-medium text-gray-700">{label}</label>
        </div>''')
        elif f.type in ("integer", "number"):
            step = '1' if f.type == "integer" else 'any'
            field_inputs.append(f'''        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{label}{" *" if f.required else ""}</label>
          <input type="number" step="{step}" name="{f.name}" defaultValue={{data?.{f.name} || ""}}{required}
            className="flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm" />
        </div>''')
        else:
            input_type = "email" if f.type == "email" else "date" if f.type in ("date", "datetime") else "text"
            field_inputs.append(f'''        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{label}{" *" if f.required else ""}</label>
          <input type="{input_type}" name="{f.name}" defaultValue={{data?.{f.name} || ""}}{required}
            className="flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm" />
        </div>''')

    fields_str = "\n".join(field_inputs)

    content = f'''"use client";

interface {cls}FormProps {{
  data?: any;
  onSubmit: (data: any) => void;
  submitLabel?: string;
}}

export function {cls}Form({{ data, onSubmit, submitLabel = "Save" }}: {cls}FormProps) {{
  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {{
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const obj: any = {{}};
    formData.forEach((v, k) => {{ if (v) obj[k] = v; }});
    onSubmit(obj);
  }}

  return (
    <form onSubmit={{handleSubmit}} className="space-y-4 max-w-lg">
{fields_str}
      <button type="submit"
        className="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700">
        {{submitLabel}}
      </button>
    </form>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/components/{cls}Form.tsx",
        content=content,
        provenance=entity.fqn,
    )


def _generate_detail_view(entity: EntityIR) -> GeneratedFile:
    cls = _to_pascal(entity.name)

    field_rows = []
    for f in entity.fields:
        if f.computed and f.name in ("id", "created_at", "updated_at"):
            continue
        label = f.name.replace("_", " ").title()
        if f.enum_values:
            field_rows.append(f'        <div><span className="text-gray-500 text-sm">{label}</span><div><Badge value={{String(data.{f.name} || "—")}} /></div></div>')
        else:
            field_rows.append(f'        <div><span className="text-gray-500 text-sm">{label}</span><div>{{String(data.{f.name} ?? "—")}}</div></div>')

    fields_str = "\n".join(field_rows)

    state_widget = ""
    if entity.state_machine:
        state_widget = '''
      {data.state && (
        <div className="mb-6">
          <Badge value={data.state} />
        </div>
      )}'''

    content = f'''"use client";
import {{ Badge }} from "@/components/ui/badge";

interface {cls}DetailProps {{
  data: any;
}}

export function {cls}Detail({{ data }}: {cls}DetailProps) {{
  return (
    <div>{state_widget}
      <div className="grid grid-cols-2 gap-4">
{fields_str}
      </div>
      <div className="mt-4 text-xs text-gray-400">
        ID: {{data.id}} | Created: {{data.created_at}}
      </div>
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/components/{cls}Detail.tsx",
        content=content,
        provenance=entity.fqn,
    )


def _generate_kanban_board(entity: EntityIR, page: PageIR) -> GeneratedFile:
    cls = _to_pascal(entity.name)
    sm = entity.state_machine
    kanban_view = next((v for v in page.views if v.get("type") == "kanban"), None)
    card_fields = kanban_view.get("card_fields", []) if kanban_view else [entity.fields[0].name if entity.fields else "id"]

    states_json = []
    for state in sm.states:
        color = {"open": "blue", "hold": "yellow", "closed": "green"}.get(state.category, "gray")
        states_json.append(f'  {{ name: "{state.name}", label: "{state.label}", color: "{color}" }}')
    states_str = ",\n".join(states_json)

    card_content = "\n".join(f'            <div className="text-sm">{{item.{cf}}}</div>' for cf in card_fields)

    content = f'''"use client";
import {{ Badge }} from "@/components/ui/badge";

const STATES = [
{states_str}
];

interface {cls}KanbanProps {{
  items: any[];
  onTransition: (id: string, newState: string) => void;
}}

export function {cls}Kanban({{ items, onTransition }}: {cls}KanbanProps) {{
  return (
    <div className="flex gap-4 overflow-x-auto pb-4">
      {{STATES.map((state) => (
        <div key={{state.name}} className="flex-shrink-0 w-72 bg-gray-50 rounded-lg p-3">
          <h3 className="font-medium mb-3 flex items-center gap-2">
            <span className={{`w-2 h-2 rounded-full bg-${{state.color}}-500`}} />
            {{state.label}}
            <span className="text-gray-400 text-sm">
              {{items.filter((i) => i.state === state.name).length}}
            </span>
          </h3>
          <div className="space-y-2">
            {{items
              .filter((item) => item.state === state.name)
              .map((item) => (
                <div key={{item.id}} className="bg-white rounded-md border p-3 shadow-sm">
{card_content}
                </div>
              ))}}
          </div>
        </div>
      ))}}
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/components/{cls}Kanban.tsx",
        content=content,
        provenance=page.fqn,
    )


def _generate_app_sidebar(ir: DomainIR) -> GeneratedFile:
    entity_map = {e.fqn: e for e in ir.entities}

    nav_items = []
    for page in ir.pages:
        entity = entity_map.get(page.entity_fqn)
        icon = entity.icon if entity and entity.icon else "file"
        label = page.title or page.name.replace("_", " ").title()
        nav_items.append(f'  {{ href: "{page.route}", label: "{label}", icon: "{icon}" }}')
    nav_str = ",\n".join(nav_items)

    content = f'''"use client";
import Link from "next/link";
import {{ usePathname }} from "next/navigation";
import {{ cn }} from "@/lib/utils";

const NAV_ITEMS = [
{nav_str}
];

export function AppSidebar() {{
  const pathname = usePathname();

  return (
    <aside className="w-64 border-r bg-white h-screen sticky top-0 flex flex-col">
      <div className="p-4 border-b">
        <h1 className="text-lg font-bold text-gray-900">{ir.domain.replace("_", " ").title()}</h1>
        <p className="text-xs text-gray-500">Powered by Specora</p>
      </div>
      <nav className="flex-1 p-2">
        {{NAV_ITEMS.map((item) => (
          <Link
            key={{item.href}}
            href={{item.href}}
            className={{cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              pathname.startsWith(item.href)
                ? "bg-blue-50 text-blue-700"
                : "text-gray-600 hover:bg-gray-50"
            )}}
          >
            {{item.label}}
          </Link>
        ))}}
      </nav>
    </aside>
  );
}}
'''
    return GeneratedFile(
        path="frontend/src/components/AppSidebar.tsx",
        content=content,
        provenance=f"domain/{ir.domain}",
    )
```

- [ ] **Step 2: Run full suite + commit**

```bash
python -m pytest tests/ -q
git add forge/targets/nextjs/gen_components.py
git commit -m "feat(#13/T3): component generators — DataTable, EntityForm, KanbanBoard, DetailView, Sidebar, shadcn primitives"
```

---

### Task 4: Page Generator

**Files:**
- Create: `forge/targets/nextjs/gen_pages.py`

Generates app router pages: list page, detail page, create page per PageIR.

- [ ] **Step 1: Implement page generator**

```python
# forge/targets/nextjs/gen_pages.py
"""Generate Next.js App Router pages from PageIR."""
from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, PageIR
from forge.targets.base import GeneratedFile


def _to_pascal(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def generate_pages(ir: DomainIR) -> list[GeneratedFile]:
    """Generate list, detail, and create pages for each PageIR."""
    files = []
    entity_map = {e.fqn: e for e in ir.entities}

    for page in ir.pages:
        entity = entity_map.get(page.entity_fqn)
        if not entity:
            continue

        route_name = page.route.strip("/")
        files.append(_generate_list_page(page, entity, route_name))
        files.append(_generate_detail_page(page, entity, route_name))
        files.append(_generate_create_page(page, entity, route_name))

    return files


def _generate_list_page(page: PageIR, entity: EntityIR, route_name: str) -> GeneratedFile:
    cls = _to_pascal(entity.name)
    has_kanban = any(v.get("type") == "kanban" for v in page.views)
    has_table = any(v.get("type") == "table" for v in page.views)

    imports = [
        '"use client";',
        'import { useEffect, useState } from "react";',
        'import { useRouter } from "next/navigation";',
        f'import {{ {page.name} }} from "@/lib/api";',
        'import { Button } from "@/components/ui/button";',
    ]
    if has_table:
        imports.append(f'import {{ {cls}Table }} from "@/components/{cls}Table";')
    if has_kanban:
        imports.append(f'import {{ {cls}Kanban }} from "@/components/{cls}Kanban";')

    view_toggle = ""
    if has_table and has_kanban:
        view_toggle = '''
        <div className="flex gap-2">
          <Button variant={view === "table" ? "default" : "outline"} size="sm" onClick={() => setView("table")}>Table</Button>
          <Button variant={view === "kanban" ? "default" : "outline"} size="sm" onClick={() => setView("kanban")}>Kanban</Button>
        </div>'''

    table_render = f'<{cls}Table items={{items}} basePath="/{route_name}" />' if has_table else ""
    kanban_render = f'<{cls}Kanban items={{items}} onTransition={{handleTransition}} />' if has_kanban else ""

    view_body = ""
    if has_table and has_kanban:
        view_body = f'''
        {{view === "table" ? {table_render} : {kanban_render}}}'''
    elif has_table:
        view_body = f"\n        {table_render}"
    elif has_kanban:
        view_body = f"\n        {kanban_render}"

    transition_handler = ""
    if has_kanban:
        transition_handler = f'''
  async function handleTransition(id: string, newState: string) {{
    await {page.name}.transition(id, newState);
    loadData();
  }}'''

    content = "\n".join(imports) + f'''

export default function {cls}ListPage() {{
  const router = useRouter();
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  {"const [view, setView] = useState<'table' | 'kanban'>('table');" if has_table and has_kanban else ""}

  async function loadData() {{
    const data = await {page.name}.list();
    setItems(data.items || []);
    setTotal(data.total || 0);
  }}

  useEffect(() => {{ loadData(); }}, []);
{transition_handler}

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">{page.title or cls}</h1>
          <p className="text-gray-500 text-sm">{{total}} total</p>
        </div>
        <div className="flex gap-2">{view_toggle}
          <Button onClick={{() => router.push("/{route_name}/new")}}>Create</Button>
        </div>
      </div>
      {view_body}
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/app/{route_name}/page.tsx",
        content=content,
        provenance=page.fqn,
    )


def _generate_detail_page(page: PageIR, entity: EntityIR, route_name: str) -> GeneratedFile:
    cls = _to_pascal(entity.name)

    content = f'''"use client";
import {{ useEffect, useState }} from "react";
import {{ useParams, useRouter }} from "next/navigation";
import {{ {page.name} }} from "@/lib/api";
import {{ {cls}Detail }} from "@/components/{cls}Detail";
import {{ Button }} from "@/components/ui/button";

export default function {cls}DetailPage() {{
  const params = useParams();
  const router = useRouter();
  const [data, setData] = useState<any>(null);

  useEffect(() => {{
    {page.name}.get(params.id as string).then(setData);
  }}, [params.id]);

  if (!data) return <div className="p-8 text-gray-500">Loading...</div>;

  async function handleDelete() {{
    if (confirm("Delete this {entity.name}?")) {{
      await {page.name}.delete(data.id);
      router.push("/{route_name}");
    }}
  }}

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">{{data.{entity.fields[0].name if entity.fields else "id"}}}</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={{() => router.push("/{route_name}")}}>Back</Button>
          <Button variant="destructive" onClick={{handleDelete}}>Delete</Button>
        </div>
      </div>
      <{cls}Detail data={{data}} />
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/app/{route_name}/[id]/page.tsx",
        content=content,
        provenance=page.fqn,
    )


def _generate_create_page(page: PageIR, entity: EntityIR, route_name: str) -> GeneratedFile:
    cls = _to_pascal(entity.name)

    content = f'''"use client";
import {{ useRouter }} from "next/navigation";
import {{ {page.name} }} from "@/lib/api";
import {{ {cls}Form }} from "@/components/{cls}Form";

export default function Create{cls}Page() {{
  const router = useRouter();

  async function handleSubmit(data: any) {{
    await {page.name}.create(data);
    router.push("/{route_name}");
  }}

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Create {cls}</h1>
      <{cls}Form onSubmit={{handleSubmit}} submitLabel="Create" />
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/app/{route_name}/new/page.tsx",
        content=content,
        provenance=page.fqn,
    )
```

- [ ] **Step 2: Run full suite + commit**

```bash
python -m pytest tests/ -q
git add forge/targets/nextjs/gen_pages.py
git commit -m "feat(#13/T4): page generator — list, detail, create pages from PageIR"
```

---

### Task 5: Layout + Dashboard Generator

**Files:**
- Create: `forge/targets/nextjs/gen_layout.py`

- [ ] **Step 1: Implement layout + dashboard**

```python
# forge/targets/nextjs/gen_layout.py
"""Generate root layout, dashboard, and Docker files."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile


def _to_pascal(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def generate_layout(ir: DomainIR) -> list[GeneratedFile]:
    return [
        _generate_root_layout(ir),
        _generate_dashboard(ir),
        _generate_dockerfile(ir),
    ]


def _generate_root_layout(ir: DomainIR) -> GeneratedFile:
    domain_title = ir.domain.replace("_", " ").title()

    content = f'''import "./globals.css";
import {{ AppSidebar }} from "@/components/AppSidebar";

export const metadata = {{
  title: "{domain_title}",
  description: "Generated by Specora",
}};

export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen bg-gray-50">
          <AppSidebar />
          <main className="flex-1 p-8">
            {{children}}
          </main>
        </div>
      </body>
    </html>
  );
}}
'''
    return GeneratedFile(path="frontend/src/app/layout.tsx", content=content, provenance=f"domain/{ir.domain}")


def _generate_dashboard(ir: DomainIR) -> GeneratedFile:
    domain_title = ir.domain.replace("_", " ").title()
    entity_map = {e.fqn: e for e in ir.entities}

    cards = []
    imports = ['"use client";', 'import { useEffect, useState } from "react";', 'import Link from "next/link";']

    for page in ir.pages:
        entity = entity_map.get(page.entity_fqn)
        if not entity:
            continue
        cls = _to_pascal(entity.name)
        route = page.route.strip("/")
        imports.append(f'import {{ {page.name} }} from "@/lib/api";')
        cards.append(f'''
        <Link href="/{route}" className="block rounded-lg border bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
          <h3 className="text-lg font-semibold">{page.title or cls}</h3>
          <p className="text-3xl font-bold mt-2">{{counts.{page.name} ?? "—"}}</p>
          <p className="text-sm text-gray-500 mt-1">total records</p>
        </Link>''')

    count_fetches = []
    for page in ir.pages:
        count_fetches.append(f'      const {page.name}Data = await {page.name}.list(1, 0);')
        count_fetches.append(f'      newCounts.{page.name} = {page.name}Data.total || 0;')

    cards_str = "\n".join(cards)
    fetches_str = "\n".join(count_fetches)
    imports_str = "\n".join(imports)

    content = f'''{imports_str}

export default function Dashboard() {{
  const [counts, setCounts] = useState<any>({{}});

  useEffect(() => {{
    async function load() {{
      const newCounts: any = {{}};
{fetches_str}
      setCounts(newCounts);
    }}
    load();
  }}, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">{domain_title}</h1>
      <p className="text-gray-500 mb-6">Dashboard</p>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
{cards_str}
      </div>
    </div>
  );
}}
'''
    return GeneratedFile(path="frontend/src/app/page.tsx", content=content, provenance=f"domain/{ir.domain}")


def _generate_dockerfile(ir: DomainIR) -> GeneratedFile:
    content = """FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
"""
    return GeneratedFile(path="frontend/Dockerfile.frontend", content=content, provenance=f"domain/{ir.domain}")
```

- [ ] **Step 2: Run full suite + commit**

```bash
python -m pytest tests/ -q
git add forge/targets/nextjs/gen_layout.py
git commit -m "feat(#13/T5): layout generator — root layout, dashboard, Dockerfile"
```

---

### Task 6: Main Generator + CLI Registration + Docker Integration

**Files:**
- Create: `forge/targets/nextjs/generator.py`
- Modify: `forge/cli/main.py` — register `nextjs` target
- Modify: `forge/targets/fastapi_prod/gen_docker.py` — add frontend service to docker-compose

- [ ] **Step 1: Implement orchestrating generator**

```python
# forge/targets/nextjs/generator.py
"""Next.js frontend generator — orchestrates all sub-generators."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import BaseGenerator, GeneratedFile
from forge.targets.nextjs.gen_api_client import generate_api_client
from forge.targets.nextjs.gen_components import generate_components
from forge.targets.nextjs.gen_layout import generate_layout
from forge.targets.nextjs.gen_pages import generate_pages
from forge.targets.nextjs.gen_scaffold import generate_scaffold


class NextJSGenerator(BaseGenerator):
    """Generates a complete Next.js 15 frontend from domain contracts."""

    def name(self) -> str:
        return "nextjs"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        if not ir.pages:
            return []

        files: list[GeneratedFile] = []

        # Project scaffold
        files.extend(generate_scaffold(ir))

        # API client
        if ir.routes:
            files.append(generate_api_client(ir))

        # Reusable components
        files.extend(generate_components(ir))

        # App router pages
        files.extend(generate_pages(ir))

        # Layout + dashboard + Docker
        files.extend(generate_layout(ir))

        # Copy types.ts from existing TypeScript generator
        from forge.targets.typescript.gen_types import TypeScriptGenerator
        ts_gen = TypeScriptGenerator()
        ts_files = ts_gen.generate(ir)
        for f in ts_files:
            files.append(GeneratedFile(
                path=f"frontend/src/lib/{f.path}",
                content=f.content,
                provenance=f.provenance,
            ))

        return files
```

- [ ] **Step 2: Register in CLI**

Read `forge/cli/main.py`, find `_get_generators`, add:

```python
from forge.targets.nextjs.generator import NextJSGenerator
```

Add to registry:
```python
"nextjs": NextJSGenerator,
```

- [ ] **Step 3: Add frontend to docker-compose**

Read `forge/targets/fastapi_prod/gen_docker.py`. In the `_generate_compose` function, add a frontend service after the healer service:

```python
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile.frontend
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: http://app:8000
    depends_on:
      - app
```

- [ ] **Step 4: Add test for full generator**

Add to `tests/test_targets/test_nextjs.py`:

```python
class TestNextJSGenerator:

    def test_generates_complete_frontend(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator
        gen = NextJSGenerator()
        files = gen.generate(helpdesk_ir)

        paths = {f.path for f in files}

        # Scaffold
        assert "frontend/package.json" in paths
        assert "frontend/tailwind.config.js" in paths

        # API client
        assert "frontend/src/lib/api.ts" in paths

        # Components
        assert "frontend/src/components/TicketTable.tsx" in paths
        assert "frontend/src/components/TicketForm.tsx" in paths
        assert "frontend/src/components/AppSidebar.tsx" in paths

        # Pages
        assert "frontend/src/app/tickets/page.tsx" in paths
        assert "frontend/src/app/tickets/[id]/page.tsx" in paths
        assert "frontend/src/app/tickets/new/page.tsx" in paths

        # Layout
        assert "frontend/src/app/layout.tsx" in paths
        assert "frontend/src/app/page.tsx" in paths

        # Docker
        assert "frontend/Dockerfile.frontend" in paths

        # Types
        assert "frontend/src/lib/types.ts" in paths

    def test_no_pages_returns_empty(self) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator
        gen = NextJSGenerator()
        files = gen.generate(DomainIR(domain="empty"))
        assert files == []
```

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`

- [ ] **Step 6: End-to-end verification**

```bash
python -m forge.cli.main forge generate domains/task_manager --target nextjs --output runtime/
ls runtime/frontend/src/app/
ls runtime/frontend/src/components/
cat runtime/frontend/src/lib/api.ts | head -20
```

- [ ] **Step 7: Commit**

```bash
git add forge/targets/nextjs/generator.py forge/cli/main.py forge/targets/fastapi_prod/gen_docker.py tests/test_targets/test_nextjs.py
git commit -m "feat(#13/T6): NextJS generator orchestrator + CLI registration + Docker integration"
```

---

## Verification Checklist

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `spc forge generate domains/task_manager --target nextjs --output runtime/` generates frontend
- [ ] `runtime/frontend/package.json` has correct dependencies
- [ ] `runtime/frontend/src/lib/api.ts` has fetch functions for all routes
- [ ] `runtime/frontend/src/app/tasks/page.tsx` exists with table + kanban
- [ ] `runtime/frontend/src/components/TaskTable.tsx` exists
- [ ] `runtime/frontend/src/components/TaskForm.tsx` exists
- [ ] `runtime/frontend/src/components/AppSidebar.tsx` has nav items
- [ ] `runtime/frontend/Dockerfile.frontend` exists
- [ ] `docker-compose.yml` includes frontend service on port 3000

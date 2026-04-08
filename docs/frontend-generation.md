# Frontend Generation

Specora Core generates a complete Next.js 15 frontend from your domain contracts. Page contracts define what to show. Entity contracts define the data model. Workflow contracts define state machines. Route contracts define the API. The generator produces a working app with typed API client, sortable data tables, drag-and-drop Kanban boards, entity forms with reference dropdowns, detail views, navigation sidebar, dashboard, and Docker deployment -- all from YAML.

---

## What Gets Generated

The `NextJSGenerator` produces approximately 26 files per domain (varies by entity count):

### Project Scaffold (6 files)

| File | Source | Description |
|------|--------|-------------|
| `frontend/package.json` | `gen_scaffold.py` | Next.js 15, React 18, Tailwind CSS, lucide-react, clsx, CVA |
| `frontend/next.config.js` | `gen_scaffold.py` | Standalone output mode for Docker |
| `frontend/tailwind.config.js` | `gen_scaffold.py` | Content path: `./src/**/*.{ts,tsx}` |
| `frontend/postcss.config.js` | `gen_scaffold.py` | Tailwind + autoprefixer |
| `frontend/tsconfig.json` | `gen_scaffold.py` | ES2017, bundler module resolution, `@/*` path alias |
| `frontend/src/lib/utils.ts` | `gen_scaffold.py` | `cn()` (clsx + tailwind-merge), `formatDate()`, `formatDateTime()`, `truncate()` |

### API Client (1 file)

| File | Source | Description |
|------|--------|-------------|
| `frontend/src/lib/api.ts` | `gen_api_client.py` | Typed fetch wrapper with methods per route contract |

### UI Primitives (7 files)

| File | Source | Description |
|------|--------|-------------|
| `frontend/src/components/ui/button.tsx` | `gen_components.py` | Variants: default, destructive, outline, ghost. Sizes: default, sm, lg |
| `frontend/src/components/ui/input.tsx` | `gen_components.py` | Styled text input with focus ring |
| `frontend/src/components/ui/badge.tsx` | `gen_components.py` | Color-mapped badges (critical=red, high=orange, medium=yellow, low=green, etc.) |
| `frontend/src/components/ui/card.tsx` | `gen_components.py` | Card, CardHeader, CardTitle, CardContent |
| `frontend/src/components/ui/select.tsx` | `gen_components.py` | Styled native select |
| `frontend/src/components/ui/table.tsx` | `gen_components.py` | Table, TableHeader, TableBody, TableRow, TableHead, TableCell |
| `frontend/src/app/globals.css` | `gen_components.py` | Tailwind directives + system font stack |

### Entity Components (3-4 per entity)

For each entity that has a Page contract:

| File | Source | Description |
|------|--------|-------------|
| `frontend/src/components/{Entity}Table.tsx` | `gen_components.py` | Sortable data table with columns from the Page contract's table view |
| `frontend/src/components/{Entity}Form.tsx` | `gen_components.py` | Create/edit form with type-mapped inputs and reference dropdowns |
| `frontend/src/components/{Entity}Detail.tsx` | `gen_components.py` | Read-only detail view with all fields |
| `frontend/src/components/{Entity}Kanban.tsx` | `gen_components.py` | Drag-and-drop Kanban board (only if entity has a state machine) |

### App Sidebar (1 file)

| File | Source | Description |
|------|--------|-------------|
| `frontend/src/components/AppSidebar.tsx` | `gen_components.py` | Navigation sidebar with links for each page, active state highlighting |

### Pages (3 per entity)

For each Page contract:

| File | Source | Description |
|------|--------|-------------|
| `frontend/src/app/{route}/page.tsx` | `gen_pages.py` | List page with table/kanban toggle, create button, total count |
| `frontend/src/app/{route}/[id]/page.tsx` | `gen_pages.py` | Detail page with back/delete buttons |
| `frontend/src/app/{route}/new/page.tsx` | `gen_pages.py` | Create page with entity form |

### Layout + Dashboard + Docker (4 files)

| File | Source | Description |
|------|--------|-------------|
| `frontend/src/app/layout.tsx` | `gen_layout.py` | Root layout: sidebar + main content area |
| `frontend/src/app/page.tsx` | `gen_layout.py` | Dashboard with entity count cards |
| `frontend/Dockerfile.frontend` | `gen_layout.py` | Multi-stage build: install, build, standalone runner |
| `frontend/.dockerignore` | `gen_layout.py` | Excludes node_modules, .next, .git |

### TypeScript Types (1 file)

| File | Source | Description |
|------|--------|-------------|
| `frontend/src/lib/types.ts` | `TypeScriptGenerator` | TypeScript interfaces for all entities |

---

## How Contracts Drive Generation

### PageIR Drives Page Generation

The Page contract defines what the list page looks like:

```yaml
# Page contract
spec:
  route: /tickets
  title: Support Tickets
  entity: entity/helpdesk/ticket
  views:
    - type: table
      default: true
      columns: [subject, priority, customer_id, assigned_agent_id]
    - type: kanban
      card_fields: [subject, priority]
```

The generator reads `views` to determine:
- **Table columns**: Which fields appear in the data table (from the `table` view's `columns`)
- **Kanban card fields**: Which fields appear on Kanban cards (from the `kanban` view's `card_fields`)
- **View toggle**: If both `table` and `kanban` views exist, a toggle button is generated

If a page has both views, the list page includes Table/Kanban toggle buttons. If only one view type exists, that view is rendered directly.

### EntityIR Drives Form and Table Generation

The Entity contract's fields determine form inputs and table cells:

| Field Type | Form Input | Table Cell |
|-----------|------------|------------|
| `string` | `<input type="text">` | Plain text |
| `text` | `<textarea>` | Plain text |
| `integer` | `<input type="number" step="1">` | Plain text |
| `number` | `<input type="number" step="any">` | Plain text |
| `boolean` | `<input type="checkbox">` | Plain text |
| `email` | `<input type="email">` | Plain text |
| `date` / `datetime` | `<input type="date">` | Plain text |
| `uuid` (with `references`) | `<select>` with options fetched from referenced entity's API | Plain text |
| Any type with `enum` | `<select>` with enum values as options | `<Badge>` (color-mapped) |

**Form field filtering:**
- Fields with `computed: true` are excluded from forms (they are auto-generated)
- Fields with `immutable: true` are excluded from forms (they cannot be changed after creation)

**Reference field dropdowns:**
The form component generates a `useEffect` hook that fetches all records from the referenced entity's API on mount. The dropdown shows the `display` field (from the reference definition) for each option, with the `id` as the value.

```yaml
# Entity contract field with reference
customer_id:
  type: uuid
  required: true
  references:
    entity: entity/helpdesk/customer
    display: name
    graph_edge: SUBMITTED_BY
```

This generates a `<select>` that:
1. On mount, calls `customers.list(1000, 0)`
2. Renders `<option key={opt.id} value={opt.id}>{opt.name || opt.id}</option>` for each customer
3. Shows "Select customer..." as the placeholder

### StateMachineIR Drives Kanban Columns and Transitions

The Workflow contract defines the Kanban board:

```yaml
# Workflow contract
spec:
  initial: new
  states:
    new:
      label: New
      category: open
    assigned:
      label: Assigned
      category: open
    resolved:
      label: Resolved
      category: closed
      terminal: true
  transitions:
    new: [assigned, closed]
    assigned: [in_progress, closed]
    in_progress: [resolved, closed]
```

The Kanban generator:

1. **Columns**: One column per state, with a colored dot (`blue` for open, `yellow` for hold, `green` for closed, `gray` for unknown) and a count badge
2. **Cards**: Each card shows the fields from `card_fields` in the Page contract's kanban view
3. **Drag-and-drop**: Cards can be dragged between columns. The `VALID_TRANSITIONS` map (generated from the workflow's `transitions`) determines which drops are allowed
4. **Visual feedback**:
   - Valid drop targets get a green border (`ring-1 ring-green-300`)
   - The active drop target gets a blue highlight (`ring-2 ring-blue-400`)
   - Invalid targets show a "no-drop" cursor
5. **Terminal states**: Cards in terminal states are not draggable (`opacity-75`, no grab cursor)
6. **State transition**: On successful drop, calls `onTransition(id, newState)` which hits the `PUT /{id}/state` endpoint

### RouteIR Drives the API Client

The Route contract defines the API surface:

```yaml
# Route contract
spec:
  entity: entity/helpdesk/ticket
  base_path: /tickets
  endpoints:
    - method: GET
      path: /
    - method: POST
      path: /
    - method: GET
      path: /{id}
    - method: PATCH
      path: /{id}
    - method: DELETE
      path: /{id}
    - method: PUT
      path: /{id}/state
```

The API client generator maps each endpoint to a named function:

| Endpoint | Generated Function |
|----------|-------------------|
| `GET /` | `list: (limit = 100, offset = 0) => _fetch(...)` |
| `GET /{id}` | `get: (id: string) => _fetch(...)` |
| `POST /` | `create: (data: any) => _fetch(...)` |
| `PATCH /{id}` | `update: (id: string, data: any) => _fetch(...)` |
| `DELETE /{id}` | `delete: (id: string) => _fetch(...)` |
| `PUT /{id}/state` | `transition: (id: string, state: string) => _fetch(...)` |

The generated client uses `NEXT_PUBLIC_API_URL` (defaults to `http://localhost:8000`) and includes `Content-Type: application/json` on all requests.

```typescript
// Generated api.ts
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const tickets = {
  list: (limit = 100, offset = 0) => _fetch(`/tickets/?limit=${limit}&offset=${offset}`),
  get: (id: string) => _fetch(`/tickets/${id}`),
  create: (data: any) => _fetch(`/tickets/`, { method: "POST", body: JSON.stringify(data) }),
  update: (id: string, data: any) => _fetch(`/tickets/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  delete: (id: string) => _fetch(`/tickets/${id}`, { method: "DELETE" }),
  transition: (id: string, state: string) => _fetch(`/tickets/${id}/state`, { method: "PUT", body: JSON.stringify({ state }) }),
};
```

---

## Component Details

### DataTable

Generated per entity. Features:
- Columns from the Page contract's table view definition
- Click row to navigate to detail page (`router.push`)
- Enum fields rendered as `<Badge>` components with color mapping
- Delete button per row with confirmation dialog
- Refresh callback after deletion

### KanbanBoard

Generated per entity that has a state machine. Features:
- HTML5 drag-and-drop (no library dependencies)
- State machine-aware: only valid transitions are droppable
- Visual drop feedback: green for valid targets, blue for active target
- Terminal state cards are non-draggable with reduced opacity
- Card count per column
- Color-coded state indicators (blue=open, yellow=hold, green=closed)

### EntityForm

Generated per entity. Features:
- Automatically maps field types to HTML input types
- Reference fields rendered as `<select>` with dynamic option loading
- Enum fields rendered as `<select>` with static options
- Boolean fields rendered as checkboxes
- Text fields rendered as `<textarea>`
- Number fields rendered with appropriate `step` attribute
- Required field indicators (`*`)
- Uses native `FormData` API (no form library dependency)
- Filters out computed and immutable fields

### DetailView

Generated per entity. Features:
- Grid layout (2 columns) showing all non-system fields
- State badge at the top (if entity has a state machine)
- Enum fields rendered as `<Badge>` components
- System fields (id, created_at) shown in small text at the bottom

### AppSidebar

Generated once per domain. Features:
- Domain title at the top
- "Powered by Specora" subtitle
- Navigation links for every Page contract
- Active page highlighting (blue background when `pathname.startsWith(item.href)`)
- Sticky positioning (stays visible on scroll)
- 256px width with border separator

---

## Docker Integration

The frontend is the 4th service in the generated Docker Compose stack:

```yaml
# Generated docker-compose.yml includes:
frontend:
  build:
    context: ./frontend
    dockerfile: Dockerfile.frontend
  ports:
    - "3000:3000"
  environment:
    - NEXT_PUBLIC_API_URL=http://backend:8000
```

The `Dockerfile.frontend` uses a multi-stage build:
1. **Builder stage**: `node:20-slim`, installs dependencies, runs `next build`
2. **Runner stage**: `node:20-slim`, copies standalone output, runs `node server.js`

The `next.config.js` uses `output: 'standalone'` which produces a self-contained Node.js server without needing the full `node_modules`.

---

## Python API

### Generate the Complete Frontend

```python
from pathlib import Path
from forge.ir.compiler import Compiler
from forge.targets.nextjs.generator import NextJSGenerator

ir = Compiler(contract_root=Path("domains/helpdesk")).compile()
gen = NextJSGenerator()
files = gen.generate(ir)

output = Path("runtime")
for f in files:
    path = output / f.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f.content)
    print(f"Generated: {f.path}")
```

### Generate Individual Parts

```python
from forge.targets.nextjs.gen_scaffold import generate_scaffold
from forge.targets.nextjs.gen_api_client import generate_api_client
from forge.targets.nextjs.gen_components import generate_components
from forge.targets.nextjs.gen_pages import generate_pages
from forge.targets.nextjs.gen_layout import generate_layout

# Scaffold only
for f in generate_scaffold(ir):
    print(f.path)

# API client only
api_file = generate_api_client(ir)
print(api_file.content)

# Components only (includes entity-specific + primitives)
for f in generate_components(ir):
    print(f.path)

# Pages only (list, detail, create per entity)
for f in generate_pages(ir):
    print(f.path)

# Layout + dashboard + Docker
for f in generate_layout(ir):
    print(f.path)
```

### Check If Frontend Will Be Generated

The generator returns an empty list if there are no Page contracts:

```python
gen = NextJSGenerator()
files = gen.generate(ir)
if not files:
    print("No Page contracts found -- skipping frontend generation")
```

---

## CLI Usage

```bash
# Generate everything (backend + database + frontend + migrations)
specora generate --target all

# Generate only the frontend
specora generate --target nextjs

# After generation, install and run
cd runtime/frontend
npm install
npm run dev
# Frontend available at http://localhost:3000
```

---

## Example: Full Generation from 3 Contracts

Given these contracts:
- `domains/shop/entities/product.contract.yaml`
- `domains/shop/routes/products.contract.yaml`
- `domains/shop/pages/products.contract.yaml`

The generator produces:

```
runtime/frontend/
  package.json
  next.config.js
  tailwind.config.js
  postcss.config.js
  tsconfig.json
  Dockerfile.frontend
  .dockerignore
  src/
    lib/
      utils.ts
      api.ts
      types.ts
    components/
      ui/
        button.tsx
        input.tsx
        badge.tsx
        card.tsx
        select.tsx
        table.tsx
      ProductTable.tsx
      ProductForm.tsx
      ProductDetail.tsx
      AppSidebar.tsx
    app/
      globals.css
      layout.tsx
      page.tsx            # Dashboard
      products/
        page.tsx          # List page
        [id]/
          page.tsx        # Detail page
        new/
          page.tsx        # Create page
```

If the product entity also has a workflow (state machine), a `ProductKanban.tsx` is generated and the list page includes a Table/Kanban toggle.

---

## Related Documentation

- [Migrations](migrations.md) -- Database migrations generated alongside the frontend
- [Production Deployment](production-deployment.md) -- Docker deployment including the frontend service
- [Contract Language Reference](contract-language-reference.md) -- Page, Entity, Workflow, and Route contract syntax
- [Architecture](architecture.md) -- How the Next.js generator fits in the target system

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
            col_cells.append(f'            <TableCell>{{String(item.{col} ?? "\u2014")}}</TableCell>')
    col_cells_str = "\n".join(col_cells)

    content = f'''"use client";
import {{ useRouter }} from "next/navigation";
import {{ Table, TableHeader, TableBody, TableRow, TableHead, TableCell }} from "@/components/ui/table";
import {{ Badge }} from "@/components/ui/badge";
import {{ {page.name} }} from "@/lib/api";

interface {cls}TableProps {{
  items: any[];
  basePath: string;
  onRefresh?: () => void;
}}

export function {cls}Table({{ items, basePath, onRefresh }}: {cls}TableProps) {{
  const router = useRouter();

  async function handleDelete(e: React.MouseEvent, id: string) {{
    e.stopPropagation();
    if (!confirm("Delete this {entity.name}?")) return;
    await {page.name}.delete(id);
    if (onRefresh) onRefresh();
  }}

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
              <button className="text-sm text-red-600 hover:underline" onClick={{(e) => handleDelete(e, item.id)}}>
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

    # Collect reference fields for the useEffect fetch
    ref_fields = [f for f in form_fields if f.reference]
    ref_imports = []
    ref_state_hooks = []
    ref_fetch_calls = []
    for rf in ref_fields:
        # Extract entity name from FQN: entity/helpdesk/customer → customers
        ref_entity = rf.reference.target_entity.split("/")[-1]
        ref_plural = ref_entity + "s"
        display = rf.reference.display_field or "name"
        ref_imports.append(f'import {{ {ref_plural} }} from "@/lib/api";')
        ref_state_hooks.append(f'  const [{ref_entity}Options, set{_to_pascal(ref_entity)}Options] = useState<any[]>([]);')
        ref_fetch_calls.append(f'    {ref_plural}.list(1000, 0).then((d: any) => set{_to_pascal(ref_entity)}Options(d.items || []));')

    field_inputs = []
    for f in form_fields:
        label = f.name.replace("_", " ").title()
        required = ' required' if f.required else ''

        if f.reference:
            # Reference field → dynamic select loading from the referenced entity's API
            ref_entity = f.reference.target_entity.split("/")[-1]
            display = f.reference.display_field or "name"
            field_inputs.append(f'''        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{label}{" *" if f.required else ""}</label>
          <select name="{f.name}" defaultValue={{data?.{f.name} || ""}}{required}
            className="flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm">
            <option value="">Select {ref_entity}...</option>
            {{{ref_entity}Options.map((opt: any) => (
              <option key={{opt.id}} value={{opt.id}}>{{opt.{display} || opt.id}}</option>
            ))}}
          </select>
        </div>''')
        elif f.enum_values:
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

    ref_imports_str = "\n".join(set(ref_imports))
    ref_hooks_str = "\n".join(ref_state_hooks)
    ref_fetches_str = "\n".join(ref_fetch_calls)

    use_effect_block = ""
    if ref_fields:
        use_effect_block = f"""
  useEffect(() => {{
{ref_fetches_str}
  }}, []);"""

    content = f'''"use client";
import {{ useState, useEffect }} from "react";
{ref_imports_str}

interface {cls}FormProps {{
  data?: any;
  onSubmit: (data: any) => void;
  submitLabel?: string;
}}

export function {cls}Form({{ data, onSubmit, submitLabel = "Save" }}: {cls}FormProps) {{
{ref_hooks_str}
{use_effect_block}

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
            field_rows.append(f'        <div><span className="text-gray-500 text-sm">{label}</span><div><Badge value={{String(data.{f.name} || "\u2014")}} /></div></div>')
        else:
            field_rows.append(f'        <div><span className="text-gray-500 text-sm">{label}</span><div>{{String(data.{f.name} ?? "\u2014")}}</div></div>')

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
        terminal = "true" if state.terminal else "false"
        states_json.append(f'  {{ name: "{state.name}", label: "{state.label}", color: "{color}", terminal: {terminal} }}')
    states_str = ",\n".join(states_json)

    # Build valid transitions map from state machine
    transitions_entries = []
    for src, targets in sm.transitions.items():
        targets_str = ", ".join(f'"{t}"' for t in targets)
        transitions_entries.append(f'  "{src}": [{targets_str}]')
    transitions_str = ",\n".join(transitions_entries)

    card_content = "\n".join(f'              <div className="text-sm">{{item.{cf}}}</div>' for cf in card_fields)

    content = f'''"use client";
import {{ useState }} from "react";
import {{ Badge }} from "@/components/ui/badge";

const STATES = [
{states_str}
];

const VALID_TRANSITIONS: Record<string, string[]> = {{
{transitions_str}
}};

interface {cls}KanbanProps {{
  items: any[];
  onTransition: (id: string, newState: string) => void;
}}

export function {cls}Kanban({{ items, onTransition }}: {cls}KanbanProps) {{
  const [dragItem, setDragItem] = useState<any>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);

  function canDrop(targetState: string): boolean {{
    if (!dragItem) return false;
    const valid = VALID_TRANSITIONS[dragItem.state] || [];
    return valid.includes(targetState);
  }}

  function handleDragStart(e: React.DragEvent, item: any) {{
    setDragItem(item);
    e.dataTransfer.effectAllowed = "move";
  }}

  function handleDragOver(e: React.DragEvent, stateName: string) {{
    e.preventDefault();
    if (canDrop(stateName)) {{
      e.dataTransfer.dropEffect = "move";
      setDragOver(stateName);
    }} else {{
      e.dataTransfer.dropEffect = "none";
    }}
  }}

  function handleDragLeave() {{
    setDragOver(null);
  }}

  function handleDrop(e: React.DragEvent, targetState: string) {{
    e.preventDefault();
    setDragOver(null);
    if (dragItem && canDrop(targetState)) {{
      onTransition(dragItem.id, targetState);
    }}
    setDragItem(null);
  }}

  function handleDragEnd() {{
    setDragItem(null);
    setDragOver(null);
  }}

  return (
    <div className="flex gap-4 overflow-x-auto pb-4">
      {{STATES.map((state) => {{
        const isValidTarget = canDrop(state.name);
        const isDraggedOver = dragOver === state.name;

        return (
          <div
            key={{state.name}}
            className={{`flex-shrink-0 w-72 rounded-lg p-3 transition-colors ${{
              isDraggedOver && isValidTarget
                ? "bg-blue-50 ring-2 ring-blue-400"
                : isValidTarget && dragItem
                ? "bg-green-50 ring-1 ring-green-300"
                : "bg-gray-50"
            }}`}}
            onDragOver={{(e) => handleDragOver(e, state.name)}}
            onDragLeave={{handleDragLeave}}
            onDrop={{(e) => handleDrop(e, state.name)}}
          >
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
                  <div
                    key={{item.id}}
                    draggable={{!state.terminal}}
                    onDragStart={{(e) => handleDragStart(e, item)}}
                    onDragEnd={{handleDragEnd}}
                    className={{`bg-white rounded-md border p-3 shadow-sm transition-all ${{
                      !state.terminal ? "cursor-grab active:cursor-grabbing hover:shadow-md" : "opacity-75"
                    }} ${{
                      dragItem?.id === item.id ? "opacity-50 ring-2 ring-blue-300" : ""
                    }}`}}
                  >
{card_content}
                  </div>
                ))}}
            </div>
          </div>
        );
      }})}}
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

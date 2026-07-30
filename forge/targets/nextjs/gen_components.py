"""Generate the reusable React components for the frontend.

Names, api bindings and URLs all come from `FrontendContext`; nothing in here
derives an identifier. Beyond that, the defects this file exists to not repeat:

  * **Write-only fields leaked into read views.** A field marked
    `sensitive: true` is present on create and update and absent from every
    response. Rendering one in a table or a detail view produces a column that
    is permanently blank, and a page contract that names one as a column is a
    contract error, not a blank column.

  * **`decimal` parsed into a JavaScript number.** A double cannot represent
    `0.1` exactly, which is the entire reason `decimal` is a distinct type.
    Decimal values cross the wire as strings and are never passed through
    `Number()`.

  * **Silent form data loss.** The old submit handler was
    `formData.forEach((v, k) => { if (v) obj[k] = v; })`. `0` and `false` are
    falsy, so an integer set to zero was dropped; and an unchecked checkbox is
    absent from `FormData` entirely, so a boolean could be turned on and never
    off. Every value is now coerced by its declared type.

  * **Failures that render as nothing.** A delete that 403s did nothing
    visible; a fetch that failed left an empty table that looked like an empty
    collection. Every mutation surfaces its outcome.
"""

from __future__ import annotations

from forge.ir.model import EntityIR, FieldIR
from forge.targets.base import GeneratedFile, GenerationError
from forge.targets.fields import creatable_fields, disclosable_fields, updatable_fields
from forge.targets.naming import camel_case
from forge.targets.nextjs.context import EntityView, FrontendContext

#: Rows fetched to build a reference field's id -> display-name map.
#:
#: There is no batch-lookup endpoint, so display names are resolved from the
#: first page of the referenced collection and anything beyond it renders as
#: explicitly unresolved. Raising this trades a slower page for a wider window.
REFERENCE_LOOKUP_LIMIT = 200

#: IR types that need a JSON editor and a JSON parse on submit.
_JSON_TYPES = frozenset({"array", "object"})


def generate_components(ctx: FrontendContext) -> list[GeneratedFile]:
    """Generate the shared primitives and one component set per entity."""
    files = [
        _shadcn_button(),
        _shadcn_input(),
        _shadcn_badge(),
        _shadcn_card(),
        _shadcn_select(),
        _shadcn_table(),
        _states(),
        _reference_value(),
        _globals_css(),
    ]

    # One set per entity, not per page: two page contracts may bind the same
    # entity, and generating the set twice would claim the same path twice.
    for view in ctx.component_views:
        files.append(_data_table(ctx, view))
        files.append(_entity_form(ctx, view))
        files.append(_detail_view(ctx, view))
        if view.entity.state_machine is not None:
            files.append(_kanban_board(ctx, view))

    files.append(_app_sidebar(ctx))
    return files


# =============================================================================
# Field classification
# =============================================================================


def _is_sensitive(field: FieldIR) -> bool:
    """Whether a field is write-only: accepted on write, never disclosed."""
    return field.sensitive


def _field_kind(field: FieldIR) -> str:
    """The coercion kind for a field, as understood by `lib/form.ts`."""
    if field.reference is not None or field.enum_values:
        return "string"
    if field.type in _JSON_TYPES:
        return "json"
    if field.type in ("integer", "number", "decimal", "boolean"):
        return field.type
    return "string"


def _label(name: str) -> str:
    return name.replace("_", " ").title()


def _table_columns(view: EntityView) -> list[str]:
    """The table's columns, validated against what the API returns."""
    table_view = next((v for v in view.page.views if v.get("type") == "table"), None)
    declared = table_view.get("columns") if table_view else None
    if not declared:
        return [f.name for f in disclosable_fields(view.entity)[:6]]

    for column in declared:
        _require_readable(view, column, "spec.views[].columns")
    return list(declared)


def _require_readable(view: EntityView, name: str, where: str) -> None:
    """Reject a page contract naming a field the API will never return.

    Both failures below used to render as a column of em-dashes for the life of
    the deployment, because the old components were typed `any`: nothing —
    not the generator, not the compiler, not the running app — could tell the
    difference between "this field is empty" and "this field does not exist".
    """
    field = next((f for f in view.entity.fields if f.name == name), None)

    if field is None:
        available = sorted(f.name for f in disclosable_fields(view.entity))
        raise GenerationError(
            f"{view.page.fqn}: {where} names {name!r}, but {view.entity.fqn} has "
            f"no such field, so the column can never show anything. "
            f"Available fields: {available}. Either add {name!r} to the entity "
            f"contract or remove it from the page contract."
        )

    if _is_sensitive(field):
        raise GenerationError(
            f"{view.page.fqn}: {where} names {name!r}, which is write-only on "
            f"{view.entity.fqn} (sensitive: true). The API never returns it, so "
            f"the column would always be blank. Remove it from the page contract."
        )


# =============================================================================
# Reference resolution
# =============================================================================


class _ReferenceBinding:
    """Everything needed to resolve one reference field to a display name.

    Attributes:
        column: The field on this entity holding the target's id.
        api: The api-client export that lists the target entity.
        binding: camelCase stem for the generated React state.
        model: The target's TypeScript interface, so fetched rows stay typed.
        display: The field on the target to show.
    """

    def __init__(
        self, column: str, api: str, binding: str, model: str, display: str
    ) -> None:
        self.column = column
        self.api = api
        self.binding = binding
        self.model = model
        self.display = display

    @property
    def state(self) -> str:
        return f"{self.binding}Names"

    @property
    def setter(self) -> str:
        return f"set{self.binding[:1].upper()}{self.binding[1:]}Names"

    @property
    def options(self) -> str:
        return f"{self.binding}Options"

    @property
    def options_setter(self) -> str:
        return f"set{self.binding[:1].upper()}{self.binding[1:]}Options"


def _reference_bindings(
    ctx: FrontendContext, entity: EntityIR, fields: list[FieldIR]
) -> list[_ReferenceBinding]:
    """Resolve the reference fields among `fields`, deduplicated by target.

    Two fields pointing at the same entity — `debit_account_id` and
    `credit_account_id` — must share one lookup. Emitting a binding per field
    declared the same `const` twice and the component did not compile.
    """
    bindings: list[_ReferenceBinding] = []
    for field in fields:
        if field.reference is None:
            continue
        target = field.reference.target_entity
        api = ctx.api_for_entity(target)
        if not api:
            # The target has no route contract, so it has no endpoint to read
            # display names from. The value renders as unresolved rather than
            # the component importing a binding that does not exist.
            continue
        model = ctx.component_for_entity(target)
        display = field.reference.display_field
        if model and not ctx.entity_has_field(target, display):
            raise GenerationError(
                f"{entity.fqn}: field {field.name!r} references {target} with "
                f"display {display!r}, but {target} has no such field. The "
                f"reference would render with nothing to show. Point "
                f"references.display at a field the target declares."
            )
        bindings.append(
            _ReferenceBinding(
                column=field.name,
                api=api,
                binding=camel_case(model or target.split("/")[-1]),
                model=model or "Record<string, unknown>",
                display=display,
            )
        )
    return bindings


def _unique_bindings(bindings: list[_ReferenceBinding]) -> list[_ReferenceBinding]:
    """One binding per target entity, preserving order."""
    seen: set[str] = set()
    unique = []
    for binding in bindings:
        if binding.binding in seen:
            continue
        seen.add(binding.binding)
        unique.append(binding)
    return unique


def _binding_for(bindings: list[_ReferenceBinding], column: str) -> _ReferenceBinding | None:
    return next((b for b in bindings if b.column == column), None)


def _reference_imports(bindings: list[_ReferenceBinding]) -> str:
    apis = sorted({b.api for b in bindings})
    if not apis:
        return ""
    return "import { " + ", ".join(apis) + ' } from "@/lib/api";'


def _type_imports(*names: str) -> str:
    """Import exactly the entity interfaces a component names."""
    wanted = sorted({n for n in names if n and n.isidentifier()})
    if not wanted:
        return ""
    return "import type { " + ", ".join(wanted) + ' } from "@/lib/types";'


def _reference_lookup_effect(bindings: list[_ReferenceBinding], indent: str = "  ") -> str:
    """The effect that populates every reference's id -> name map."""
    unique = _unique_bindings(bindings)
    if not unique:
        return ""

    lines = [f"{indent}useEffect(() => {{"]
    for binding in unique:
        lines.extend(
            [
                f"{indent}  {binding.api}",
                f"{indent}    .list({{ limit: {REFERENCE_LOOKUP_LIMIT} }})",
                f"{indent}    .then((page) => {{",
                f"{indent}      const names: Record<string, string> = {{}};",
                f"{indent}      for (const row of page.items) {{",
                f"{indent}        if (row.id === undefined || row.id === null) continue;",
                f"{indent}        const label = row.{binding.display};",
                f"{indent}        names[String(row.id)] =",
                f'{indent}          typeof label === "string" && label !== ""',
                f"{indent}            ? label",
                f"{indent}            : String(row.id);",
                f"{indent}      }}",
                f"{indent}      {binding.setter}(names);",
                f"{indent}    }})",
                f"{indent}    .catch((cause) => {{",
                f"{indent}      // A failed lookup leaves references unresolved, which the",
                f"{indent}      // ReferenceValue component shows as such. It must not take",
                f"{indent}      // the whole view down with it.",
                f'{indent}      console.error("Could not resolve {binding.binding} names", cause);',
                f"{indent}    }});",
            ]
        )
    lines.append(f"{indent}}}, []);")
    return "\n".join(lines)


def _reference_state(bindings: list[_ReferenceBinding], indent: str = "  ") -> str:
    return "\n".join(
        f"{indent}const [{b.state}, {b.setter}] = useState<Record<string, string>>({{}});"
        for b in _unique_bindings(bindings)
    )


# =============================================================================
# shadcn-style primitives
# =============================================================================


def _shadcn_button() -> GeneratedFile:
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
          "inline-flex items-center justify-center rounded-md font-medium",
          "transition-colors focus-visible:outline-none",
          "disabled:pointer-events-none disabled:opacity-50",
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
    return GeneratedFile(
        path="frontend/src/components/ui/button.tsx", content=content, provenance="shadcn/ui"
    )


def _shadcn_input() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";
import { forwardRef, type InputHTMLAttributes } from "react";

const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2",
        "text-sm placeholder:text-gray-400",
        "focus:outline-none focus:ring-2 focus:ring-blue-500",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";
export { Input };
'''
    return GeneratedFile(
        path="frontend/src/components/ui/input.tsx", content=content, provenance="shadcn/ui"
    )


def _shadcn_badge() -> GeneratedFile:
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
  if (!value) return <span className="text-gray-400">&mdash;</span>;
  const color = colorMap[value] || colorMap.default;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        color,
        className
      )}
    >
      {value}
    </span>
  );
}
'''
    return GeneratedFile(
        path="frontend/src/components/ui/badge.tsx", content=content, provenance="shadcn/ui"
    )


def _shadcn_card() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";

interface SlotProps {
  children: React.ReactNode;
  className?: string;
}

export function Card({ children, className }: SlotProps) {
  return (
    <div className={cn("rounded-lg border bg-white p-6 shadow-sm", className)}>
      {children}
    </div>
  );
}

export function CardHeader({ children, className }: SlotProps) {
  return <div className={cn("mb-4", className)}>{children}</div>;
}

export function CardTitle({ children, className }: SlotProps) {
  return <h3 className={cn("text-lg font-semibold", className)}>{children}</h3>;
}

export function CardContent({ children, className }: SlotProps) {
  return <div className={className}>{children}</div>;
}
'''
    return GeneratedFile(
        path="frontend/src/components/ui/card.tsx", content=content, provenance="shadcn/ui"
    )


def _shadcn_select() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";
import { forwardRef, type SelectHTMLAttributes } from "react";

const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2",
        "text-sm focus:outline-none focus:ring-2 focus:ring-blue-500",
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
    return GeneratedFile(
        path="frontend/src/components/ui/select.tsx", content=content, provenance="shadcn/ui"
    )


def _shadcn_table() -> GeneratedFile:
    content = '''import { cn } from "@/lib/utils";

interface SlotProps {
  children: React.ReactNode;
  className?: string;
}

export function Table({ children, className }: SlotProps) {
  // The wrapper scrolls, not the page: a wide table must not push the whole
  // layout sideways on a narrow viewport.
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full caption-bottom text-sm", className)}>{children}</table>
    </div>
  );
}
export function TableHeader({ children }: { children: React.ReactNode }) {
  return <thead className="border-b bg-gray-50">{children}</thead>;
}
export function TableBody({ children }: { children: React.ReactNode }) {
  return <tbody className="divide-y">{children}</tbody>;
}
export function TableRow({
  children,
  className,
  onClick,
}: SlotProps & { onClick?: () => void }) {
  return (
    <tr
      className={cn(onClick && "cursor-pointer hover:bg-gray-50", className)}
      onClick={onClick}
    >
      {children}
    </tr>
  );
}
export function TableHead({ children, className }: SlotProps) {
  return (
    <th className={cn("h-12 px-4 text-left font-medium text-gray-500", className)}>
      {children}
    </th>
  );
}
export function TableCell({ children, className }: SlotProps) {
  return <td className={cn("px-4 py-3", className)}>{children}</td>;
}
'''
    return GeneratedFile(
        path="frontend/src/components/ui/table.tsx", content=content, provenance="shadcn/ui"
    )


def _states() -> GeneratedFile:
    """Shared loading/empty/error panels.

    A failed request used to leave the same empty table an empty collection
    does, so "the server is down" and "you have no tickets" looked identical.
    """
    content = '''"use client";
import { Button } from "@/components/ui/button";

export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return <div className="py-12 text-center text-sm text-gray-500">{label}</div>;
}

export function EmptyState({ label }: { label: string }) {
  return <div className="py-12 text-center text-sm text-gray-500">{label}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-4">
      <p className="text-sm text-red-700">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function InlineError({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
      {message}
    </p>
  );
}
'''
    return GeneratedFile(
        path="frontend/src/components/ui/states.tsx", content=content, provenance="shadcn/ui"
    )


def _reference_value() -> GeneratedFile:
    """Render a foreign key as its display name, or as visibly unresolved."""
    content = '''import { cn } from "@/lib/utils";

/**
 * A reference rendered as the target's display name.
 *
 * Display names come from the first page of the referenced collection — the
 * API has no batch lookup — so an id outside that page cannot be resolved.
 * Falling back to the raw identifier would put a UUID on screen looking like
 * data; this says plainly that the name is missing and keeps the id in the
 * tooltip for whoever needs it.
 */
export function ReferenceValue({
  id,
  names,
  className,
}: {
  id: unknown;
  names: Record<string, string>;
  className?: string;
}) {
  if (id === null || id === undefined || id === "") {
    return <span className={cn("text-gray-400", className)}>&mdash;</span>;
  }

  const key = String(id);
  const label = names[key];
  if (label) return <span className={className}>{label}</span>;

  return (
    <span
      className={cn("italic text-gray-400", className)}
      title={`Unresolved reference: ${key}`}
    >
      unresolved
    </span>
  );
}
'''
    return GeneratedFile(
        path="frontend/src/components/ui/reference.tsx",
        content=content,
        provenance="domain/frontend",
    )


def _globals_css() -> GeneratedFile:
    content = """@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
"""
    return GeneratedFile(
        path="frontend/src/app/globals.css", content=content, provenance="shadcn/ui"
    )


# =============================================================================
# Entity components
# =============================================================================


def _data_table(ctx: FrontendContext, view: EntityView) -> GeneratedFile:
    entity = view.entity
    cls = view.component
    columns = _table_columns(view)
    by_name = {f.name: f for f in entity.fields}

    bindings = _reference_bindings(
        ctx, entity, [by_name[c] for c in columns if c in by_name]
    )

    header_cells = "\n".join(
        f"            <TableHead>{_label(column)}</TableHead>" for column in columns
    )

    body_cells = []
    for column in columns:
        field = by_name.get(column)
        binding = _binding_for(bindings, column)
        if binding is not None:
            body_cells.append(
                f"              <TableCell>\n"
                f"                <ReferenceValue id={{item.{column}}} "
                f"names={{{binding.state}}} />\n"
                f"              </TableCell>"
            )
        elif field is not None and field.enum_values:
            body_cells.append(
                f"              <TableCell>"
                f'<Badge value={{item.{column} == null ? "" : String(item.{column})}} />'
                f"</TableCell>"
            )
        elif field is not None and field.type in ("datetime", "date"):
            formatter = "formatDateTime" if field.type == "datetime" else "formatDate"
            body_cells.append(
                f"              <TableCell>"
                f"{{{formatter}(item.{column} as string | null | undefined)}}"
                f"</TableCell>"
            )
        else:
            # `decimal` lands here and is rendered as the string the API sent.
            # Passing it through Number() would round it.
            body_cells.append(
                f"              <TableCell>"
                f'{{item.{column} == null ? "\\u2014" : String(item.{column})}}'
                f"</TableCell>"
            )
    body_cells_src = "\n".join(body_cells)

    needs_dates = any(
        by_name.get(c) is not None and by_name[c].type in ("datetime", "date")
        for c in columns
    )
    imports = [
        '"use client";',
        'import { useEffect, useState } from "react";',
        'import { useRouter } from "next/navigation";',
        "import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }"
        ' from "@/components/ui/table";',
    ]
    if any(by_name.get(c) is not None and by_name[c].enum_values for c in columns):
        imports.append('import { Badge } from "@/components/ui/badge";')
    if bindings:
        imports.append('import { ReferenceValue } from "@/components/ui/reference";')
        imports.append(_reference_imports(bindings))
    if needs_dates:
        formatters = sorted(
            {
                "formatDateTime" if by_name[c].type == "datetime" else "formatDate"
                for c in columns
                if by_name.get(c) is not None and by_name[c].type in ("datetime", "date")
            }
        )
        imports.append("import { " + ", ".join(formatters) + ' } from "@/lib/utils";')
    imports.append(_type_imports(cls, *(b.model for b in bindings)))
    imports_src = "\n".join(i for i in imports if i)

    state_src = _reference_state(bindings)
    effect_src = _reference_lookup_effect(bindings)
    # `useEffect`/`useState` are only used by the reference lookup.
    if not bindings:
        imports_src = imports_src.replace(
            'import { useEffect, useState } from "react";\n', ""
        )

    content = f'''{imports_src}

interface {cls}TableProps {{
  items: {cls}[];
  basePath: string;
  onDelete?: (id: string) => void;
}}

export function {cls}Table({{ items, basePath, onDelete }}: {cls}TableProps) {{
  const router = useRouter();
{state_src}
{effect_src}

  return (
    <Table>
      <TableHeader>
        <TableRow>
{header_cells}
            <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {{items.map((item) => (
          <TableRow
            key={{String(item.id)}}
            onClick={{() => router.push(`${{basePath}}/${{encodeURIComponent(String(item.id))}}`)}}
          >
{body_cells_src}
            <TableCell>
              {{onDelete && (
                <button
                  type="button"
                  className="text-sm text-red-600 hover:underline"
                  onClick={{(event) => {{
                    event.stopPropagation();
                    onDelete(String(item.id));
                  }}}}
                >
                  Delete
                </button>
              )}}
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
        provenance=view.page.fqn,
    )


def _entity_form(ctx: FrontendContext, view: EntityView) -> GeneratedFile:
    entity = view.entity
    cls = view.component
    # `immutable` means "cannot change after creation", so an immutable field
    # belongs on the create form and must be absent from the edit form. Folding
    # the two into one set dropped it from both, which for an all-immutable
    # entity like entity/financial_ledger/audit_event produced a create form
    # with no inputs at all.
    fields = creatable_fields(entity)
    editable = {f.name for f in updatable_fields(entity)}
    bindings = _unique_bindings(_reference_bindings(ctx, entity, fields))

    always, create_only = [], []
    for field in fields:
        required = "true" if field.required else "false"
        if field.sensitive and field.required:
            # On an edit the stored value is never returned, so demanding it
            # again would make every edit require re-entering the secret.
            required = "!isEdit"
        spec = (
            f'{field.name}: {{ kind: "{_field_kind(field)}", '
            f'required: {required}, label: "{_label(field.name)}" }},'
        )
        (always if field.name in editable else create_only).append(spec)

    specs_src = "\n".join(f"    {spec}" for spec in always)
    if create_only:
        # Omitted on edit so a field the form does not render cannot be
        # reported as a missing required value.
        nested = "\n".join(f"          {spec}" for spec in create_only)
        specs_src += (
            "\n    ...(isEdit\n      ? {}\n      : {\n"
            f"{nested}\n"
            "        }),"
        )

    option_state = "\n".join(
        f"  const [{b.options}, {b.options_setter}] = useState<{b.model}[]>([]);"
        for b in bindings
    )
    option_effect = ""
    if bindings:
        lines = ["  useEffect(() => {"]
        for binding in bindings:
            lines.extend(
                [
                    f"    {binding.api}",
                    f"      .list({{ limit: {REFERENCE_LOOKUP_LIMIT} }})",
                    f"      .then((page) => {binding.options_setter}(page.items))",
                    "      .catch((cause) => {",
                    f'        console.error("Could not load {binding.binding} options", cause);',
                    "        setLoadError("
                    '"Some choices could not be loaded. Reload to try again.");',
                    "      });",
                ]
            )
        lines.append("  }, []);")
        option_effect = "\n".join(lines)

    rendered = []
    for field in fields:
        control = _form_input(ctx, entity, field)
        if field.name not in editable:
            control = (
                "      {!isEdit && (\n"
                + "\n".join("  " + line for line in control.splitlines())
                + "\n      )}"
            )
        rendered.append(control)
    inputs = "\n".join(rendered)

    imports = ['"use client";', 'import { useState } from "react";']
    if bindings:
        imports = ['"use client";', 'import { useEffect, useState } from "react";']
        imports.append(_reference_imports(bindings))
    imports.append('import { InlineError } from "@/components/ui/states";')
    imports.append('import { Button } from "@/components/ui/button";')
    imports.append('import { coerceForm, type FieldSpec } from "@/lib/form";')
    imports.append(_type_imports(cls, *(b.model for b in bindings)))
    imports_src = "\n".join(i for i in imports if i)

    content = f'''{imports_src}

/**
 * How each field is validated and converted before it is sent.
 *
 * `isEdit` only affects write-only fields: the API never returns them, so an
 * edit that leaves one blank means "unchanged", not "clear it".
 */
function fieldSpecs(isEdit: boolean): Record<string, FieldSpec> {{
  return {{
{specs_src}
  }};
}}

interface {cls}FormProps {{
  data?: {cls};
  onSubmit: (values: Record<string, unknown>) => Promise<void> | void;
  submitLabel?: string;
}}

export function {cls}Form({{ data, onSubmit, submitLabel = "Save" }}: {cls}FormProps) {{
  const isEdit = data !== undefined;
  const [errors, setErrors] = useState<Record<string, string>>({{}});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
{option_state}
{option_effect}

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {{
    event.preventDefault();
    if (pending) return;

    const {{ values, errors: found }} = coerceForm(event.currentTarget, fieldSpecs(isEdit));
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setPending(true);
    try {{
      await onSubmit(values);
    }} finally {{
      // The caller owns the failure message; this only has to stop the form
      // from staying disabled after one.
      setPending(false);
    }}
  }}

  return (
    <form onSubmit={{handleSubmit}} noValidate className="max-w-lg space-y-4">
      {{loadError !== null && <InlineError message={{loadError}} />}}
{inputs}
      <Button type="submit" disabled={{pending}}>
        {{pending ? "Saving..." : submitLabel}}
      </Button>
    </form>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/components/{cls}Form.tsx",
        content=content,
        provenance=entity.fqn,
    )


def _form_input(ctx: FrontendContext, entity: EntityIR, field: FieldIR) -> str:
    """Emit one labelled control, with the contract's constraints applied."""
    label = _label(field.name)
    marker = " *" if field.required else ""
    name = field.name
    error_line = (
        f'      {{errors.{name} && <p role="alert" className="mt-1 text-sm text-red-600">'
        f"{{errors.{name}}}</p>}}"
    )

    def wrap(control: str, *, label_for: str = name) -> str:
        return (
            f"      <div>\n"
            f'        <label htmlFor="{label_for}" className="mb-1 block text-sm '
            f'font-medium text-gray-700">{label}{marker}</label>\n'
            f"{control}\n"
            f"{error_line}\n"
            f"      </div>"
        )

    required_attr = " required" if field.required else ""
    constraints = _input_constraints(field)
    input_class = (
        'className="flex h-10 w-full rounded-md border border-gray-300 bg-white '
        'px-3 py-2 text-sm"'
    )

    if field.reference is not None:
        target = field.reference.target_entity
        api = ctx.api_for_entity(target)
        if api:
            binding = camel_case(ctx.component_for_entity(target) or target.split("/")[-1])
            display = field.reference.display_field
            control = (
                f'        <select id="{name}" name="{name}" '
                f'defaultValue={{String(data?.{name} ?? "")}}{required_attr}\n'
                f"          {input_class}>\n"
                f'          <option value="">Select...</option>\n'
                f"          {{{binding}Options.map((option) => (\n"
                f"            <option key={{String(option.id)}} value={{String(option.id)}}>\n"
                f'              {{typeof option.{display} === "string"\n'
                f'                && option.{display} !== ""\n'
                f"                ? String(option.{display})\n"
                f"                : String(option.id)}}\n"
                f"            </option>\n"
                f"          ))}}\n"
                f"        </select>"
            )
            return wrap(control)
        # No route for the target, so there is nothing to populate a picker
        # from; the identifier is entered directly rather than silently
        # offering an empty dropdown.
        control = (
            f'        <input id="{name}" name="{name}" type="text" '
            f'defaultValue={{String(data?.{name} ?? "")}}{required_attr}\n'
            f'          placeholder="Identifier"\n'
            f"          {input_class} />"
        )
        return wrap(control)

    if field.enum_values:
        options = "\n".join(
            f'          <option value="{value}">{value}</option>' for value in field.enum_values
        )
        control = (
            f'        <select id="{name}" name="{name}" '
            f'defaultValue={{String(data?.{name} ?? "")}}{required_attr}\n'
            f"          {input_class}>\n"
            f'          <option value="">Select...</option>\n'
            f"{options}\n"
            f"        </select>"
        )
        return wrap(control)

    if _is_sensitive(field):
        # Never prefilled: the API does not return the stored value, so there
        # is nothing to prefill with, and a blank on edit means "unchanged"
        # rather than "clear it" — which is why `required` is dropped there
        # even for a field the entity declares required.
        required_on_create = (
            "\n          {...(isEdit ? {} : { required: true })}" if field.required else ""
        )
        control = (
            f'        <input id="{name}" name="{name}" type="password"\n'
            f'          autoComplete="new-password"{constraints}\n'
            f'          placeholder={{isEdit ? "Leave blank to keep unchanged" : ""}}'
            f"{required_on_create}\n"
            f"          {input_class} />"
        )
        return wrap(control)

    if field.type in _JSON_TYPES:
        control = (
            f'        <textarea id="{name}" name="{name}"{required_attr}\n'
            f"          defaultValue={{\n"
            f"            data?.{name} === undefined\n"
            f'              ? ""\n'
            f"              : JSON.stringify(data.{name}, null, 2)\n"
            f"          }}\n"
            f'          spellCheck={{false}}\n'
            f'          className="flex min-h-[100px] w-full rounded-md border '
            f'border-gray-300 bg-white px-3 py-2 font-mono text-sm" />'
        )
        return wrap(control)

    if field.type == "text":
        control = (
            f'        <textarea id="{name}" name="{name}" '
            f'defaultValue={{String(data?.{name} ?? "")}}{required_attr}{constraints}\n'
            f'          className="flex min-h-[100px] w-full rounded-md border '
            f'border-gray-300 bg-white px-3 py-2 text-sm" />'
        )
        return wrap(control)

    if field.type == "boolean":
        return (
            f'      <div className="flex items-center gap-2">\n'
            f'        <input id="{name}" type="checkbox" name="{name}" '
            f"defaultChecked={{data?.{name} === true}} className=\"h-4 w-4\" />\n"
            f'        <label htmlFor="{name}" className="text-sm font-medium '
            f'text-gray-700">{label}</label>\n'
            f"      </div>"
        )

    if field.type in ("integer", "number", "decimal"):
        # A decimal is typed as text, not number: `<input type="number">` hands
        # back a value already round-tripped through a double.
        input_type = "text" if field.type == "decimal" else "number"
        step = ""
        if field.type == "integer":
            step = ' step="1"'
        elif field.type == "number":
            step = ' step="any"'
        extra = ' inputMode="decimal"' if field.type == "decimal" else ""
        control = (
            f'        <input id="{name}" type="{input_type}"{step}{extra} name="{name}" '
            f'defaultValue={{String(data?.{name} ?? "")}}{required_attr}{constraints}\n'
            f"          {input_class} />"
        )
        return wrap(control)

    input_type = {
        "email": "email",
        "date": "date",
        "datetime": "datetime-local",
        "uuid": "text",
    }.get(field.type, "text")
    control = (
        f'        <input id="{name}" type="{input_type}" name="{name}" '
        f'defaultValue={{String(data?.{name} ?? "")}}{required_attr}{constraints}\n'
        f"          {input_class} />"
    )
    return wrap(control)


def _input_constraints(field: FieldIR) -> str:
    """Render a field's contract constraints as HTML validation attributes.

    Unbounded free text reaching the API is what §6 of the codegen contract
    rules out; a `maxLength` in the contract has to reach the control.
    """
    attrs = []
    constraints = field.constraints or {}
    if "maxLength" in constraints:
        attrs.append(f' maxLength={{{int(constraints["maxLength"])}}}')
    if "minLength" in constraints:
        attrs.append(f' minLength={{{int(constraints["minLength"])}}}')
    if "min" in constraints:
        attrs.append(f' min={{{constraints["min"]}}}')
    if "max" in constraints:
        attrs.append(f' max={{{constraints["max"]}}}')
    if "pattern" in constraints:
        escaped = str(constraints["pattern"]).replace("\\", "\\\\").replace('"', '\\"')
        attrs.append(f' pattern="{escaped}"')
    return "".join(attrs)


def _detail_view(ctx: FrontendContext, view: EntityView) -> GeneratedFile:
    entity = view.entity
    cls = view.component
    fields = disclosable_fields(entity)
    bindings = _reference_bindings(ctx, entity, fields)

    rows = []
    for field in fields:
        if field.name in ("id", "created_at", "updated_at"):
            continue
        label = _label(field.name)
        binding = _binding_for(bindings, field.name)
        if binding is not None:
            value = (
                f"<ReferenceValue id={{data.{field.name}}} names={{{binding.state}}} />"
            )
        elif field.enum_values:
            value = (
                f'<Badge value={{data.{field.name} == null '
                f'? "" : String(data.{field.name})}} />'
            )
        elif field.type == "datetime":
            value = f"{{formatDateTime(data.{field.name} as string | null | undefined)}}"
        elif field.type == "date":
            value = f"{{formatDate(data.{field.name} as string | null | undefined)}}"
        elif field.type in _JSON_TYPES:
            value = (
                f'<pre className="whitespace-pre-wrap font-mono text-xs">'
                f'{{data.{field.name} === undefined ? "\\u2014" : '
                f"JSON.stringify(data.{field.name}, null, 2)}}</pre>"
            )
        else:
            value = f'{{data.{field.name} == null ? "\\u2014" : String(data.{field.name})}}'
        rows.append(
            f'        <div>\n'
            f'          <span className="text-sm text-gray-500">{label}</span>\n'
            f"          <div>{value}</div>\n"
            f"        </div>"
        )
    rows_src = "\n".join(rows)

    imports = ['"use client";']
    if bindings:
        imports.append('import { useEffect, useState } from "react";')
        imports.append('import { ReferenceValue } from "@/components/ui/reference";')
        imports.append(_reference_imports(bindings))
    if any(f.enum_values for f in fields) or entity.state_machine is not None:
        imports.append('import { Badge } from "@/components/ui/badge";')
    date_formatters = sorted(
        {
            "formatDateTime" if f.type == "datetime" else "formatDate"
            for f in fields
            if f.type in ("datetime", "date")
        }
    )
    # created_at is rendered in the footer whenever the entity declares it.
    has_created_at = any(f.name == "created_at" for f in fields)
    needed = set(date_formatters)
    if has_created_at:
        needed.add("formatDateTime")
    if needed:
        imports.append("import { " + ", ".join(sorted(needed)) + ' } from "@/lib/utils";')
    imports.append(_type_imports(cls, *(b.model for b in bindings)))
    imports_src = "\n".join(i for i in imports if i)

    state_src = _reference_state(bindings)
    effect_src = _reference_lookup_effect(bindings)

    state_widget = ""
    if entity.state_machine is not None:
        state_widget = """
      {data.state !== undefined && (
        <div className="mb-6">
          <Badge value={String(data.state)} />
        </div>
      )}"""

    # Only what the entity declares: an interface has no `created_at` unless a
    # mixin put one there, and reading a field off it that does not exist does
    # not compile.
    footer_parts = []
    if any(f.name == "id" for f in fields):
        footer_parts.append('        ID: {String(data.id)}')
    if has_created_at:
        footer_parts.append(
            '        Created: {formatDateTime(data.created_at)}'
        )
    footer_src = ""
    if footer_parts:
        joined = "\n        &middot;\n".join(footer_parts)
        footer_src = (
            '      <div className="mt-6 text-xs text-gray-400">\n'
            f"{joined}\n"
            "      </div>"
        )

    content = f'''{imports_src}

interface {cls}DetailProps {{
  data: {cls};
}}

export function {cls}Detail({{ data }}: {cls}DetailProps) {{
{state_src}
{effect_src}

  return (
    <div>{state_widget}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
{rows_src}
      </div>
{footer_src}
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/components/{cls}Detail.tsx",
        content=content,
        provenance=entity.fqn,
    )


#: Full Tailwind class names per workflow state category.
#:
#: Built as complete strings because Tailwind scans source for literal class
#: names; the previous `bg-${state.color}-500` was never emitted into the
#: stylesheet, so every column marker rendered invisible.
_CATEGORY_DOT = {
    "open": "bg-blue-500",
    "hold": "bg-yellow-500",
    "closed": "bg-green-500",
}


def _kanban_board(ctx: FrontendContext, view: EntityView) -> GeneratedFile:
    entity = view.entity
    cls = view.component
    machine = entity.state_machine
    kanban_view = next((v for v in view.page.views if v.get("type") == "kanban"), None)
    card_fields = (kanban_view or {}).get("card_fields") or [
        f.name for f in disclosable_fields(entity)[:1]
    ]

    for name in card_fields:
        _require_readable(view, name, "spec.views[].card_fields")

    states_src = ",\n".join(
        f'  {{ name: "{state.name}", label: "{state.label or state.name}", '
        f'dot: "{_CATEGORY_DOT.get(state.category, "bg-gray-500")}", '
        f"terminal: {str(state.terminal).lower()} }}"
        for state in machine.states
    )
    transitions_src = ",\n".join(
        f'  "{source}": [{", ".join(chr(34) + t + chr(34) for t in targets)}]'
        for source, targets in machine.transitions.items()
    )

    card_lines = []
    for index, name in enumerate(card_fields):
        style = (
            "font-medium text-sm" if index == 0 else "text-sm text-gray-600"
        )
        card_lines.append(
            f'                        <div className="{style}">'
            f'{{item.{name} == null ? "" : String(item.{name})}}</div>'
        )
    card_src = "\n".join(card_lines)

    content = f'''"use client";
import {{ useState }} from "react";
import Link from "next/link";

import type {{ {cls} }} from "@/lib/types";

const STATES = [
{states_src}
];

const VALID_TRANSITIONS: Record<string, string[]> = {{
{transitions_src}
}};

interface {cls}KanbanProps {{
  items: {cls}[];
  basePath: string;
  onTransition: (id: string, newState: string) => void;
}}

export function {cls}Kanban({{ items, basePath, onTransition }}: {cls}KanbanProps) {{
  const [dragItem, setDragItem] = useState<{cls} | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);

  function canDrop(targetState: string): boolean {{
    if (dragItem === null) return false;
    // The same transition table the server enforces, so an impossible drop is
    // refused before it becomes a 409.
    return (VALID_TRANSITIONS[String(dragItem.state)] || []).includes(targetState);
  }}

  function handleDragStart(event: React.DragEvent, item: {cls}) {{
    setDragItem(item);
    event.dataTransfer.effectAllowed = "move";
  }}

  function handleDragOver(event: React.DragEvent, stateName: string) {{
    event.preventDefault();
    if (canDrop(stateName)) {{
      event.dataTransfer.dropEffect = "move";
      setDragOver(stateName);
    }} else {{
      event.dataTransfer.dropEffect = "none";
    }}
  }}

  function handleDrop(event: React.DragEvent, targetState: string) {{
    event.preventDefault();
    setDragOver(null);
    if (dragItem !== null && canDrop(targetState)) {{
      onTransition(String(dragItem.id), targetState);
    }}
    setDragItem(null);
  }}

  return (
    <div className="flex min-w-0 gap-4 overflow-x-auto pb-4">
      {{STATES.map((state) => {{
        const isValidTarget = canDrop(state.name);
        const isDraggedOver = dragOver === state.name;
        const inState = items.filter((item) => String(item.state) === state.name);

        return (
          <div
            key={{state.name}}
            className={{`w-72 flex-shrink-0 rounded-lg p-3 transition-colors ${{
              isDraggedOver && isValidTarget
                ? "bg-blue-50 ring-2 ring-blue-400"
                : isValidTarget && dragItem
                  ? "bg-green-50 ring-1 ring-green-300"
                  : "bg-gray-50"
            }}`}}
            onDragOver={{(event) => handleDragOver(event, state.name)}}
            onDragLeave={{() => setDragOver(null)}}
            onDrop={{(event) => handleDrop(event, state.name)}}
          >
            <h3 className="mb-3 flex items-center gap-2 font-medium">
              <span className={{`h-2 w-2 rounded-full ${{state.dot}}`}} />
              {{state.label}}
              <span className="text-sm text-gray-400">{{inState.length}}</span>
            </h3>
            <div className="space-y-2">
              {{inState.map((item) => (
                <div
                  key={{String(item.id)}}
                  draggable={{!state.terminal}}
                  onDragStart={{(event) => handleDragStart(event, item)}}
                  onDragEnd={{() => {{
                    setDragItem(null);
                    setDragOver(null);
                  }}}}
                  className={{`rounded-md border bg-white p-3 shadow-sm transition-all ${{
                    state.terminal
                      ? "opacity-75"
                      : "cursor-grab hover:shadow-md active:cursor-grabbing"
                  }} ${{dragItem?.id === item.id ? "opacity-50 ring-2 ring-blue-300" : ""}}`}}
                >
                  <Link
                    href={{`${{basePath}}/${{encodeURIComponent(String(item.id))}}`}}
                    className="block hover:underline"
                  >
{card_src}
                  </Link>
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
        provenance=view.page.fqn,
    )


def _app_sidebar(ctx: FrontendContext) -> GeneratedFile:
    nav_src = ",\n".join(
        f'  {{ href: "{view.url}", label: "{_nav_label(view)}" }}' for view in ctx.views
    )
    title = ctx.ir.domain.replace("_", " ").title()

    sign_out_import = ""
    sign_out_state = ""
    sign_out_handler = ""
    sign_out_block = ""
    if ctx.auth is not None:
        sign_out_import = (
            'import { useState } from "react";\n'
            'import { signOut } from "@/lib/session";'
        )
        sign_out_state = """  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
"""
        sign_out_handler = """
  async function handleSignOut() {
    setSigningOut(true);
    setSignOutError(null);
    // Only /auth/logout can clear the httpOnly cookie. If it did not succeed
    // the session is still live, so showing the sign-in page would be a lie —
    // the next navigation would refresh straight off that cookie.
    if (!(await signOut())) {
      setSigningOut(false);
      setSignOutError("Sign-out did not complete. Check your connection and try again.");
    }
  }
"""
        sign_out_block = '''
      <div className="border-t p-2">
        {signOutError !== null && (
          <p role="alert" className="px-3 pb-2 text-xs text-red-600">
            {signOutError}
          </p>
        )}
        <button
          type="button"
          onClick={handleSignOut}
          disabled={signingOut}
          className={cn(
            "w-full rounded-md px-3 py-2 text-left text-sm font-medium",
            "text-gray-600 hover:bg-gray-50 disabled:opacity-50",
          )}
        >
          {signingOut ? "Signing out..." : "Sign out"}
        </button>
      </div>'''

    content = f'''"use client";
import Link from "next/link";
import {{ usePathname }} from "next/navigation";

import {{ cn }} from "@/lib/utils";
{sign_out_import}

const NAV_ITEMS = [
{nav_src}
];

export function AppSidebar() {{
  const pathname = usePathname();
{sign_out_state}{sign_out_handler}
  return (
    <aside className="sticky top-0 flex h-screen w-64 flex-col border-r bg-white">
      <div className="border-b p-4">
        <h1 className="text-lg font-bold text-gray-900">{title}</h1>
        <p className="text-xs text-gray-500">Powered by Specora</p>
      </div>
      <nav className="flex-1 overflow-y-auto p-2">
        {{NAV_ITEMS.map((item) => {{
          // Exact match or a path segment boundary: a plain startsWith marks
          // /tickets active while sitting on /tickets_archive.
          const active =
            pathname === item.href || pathname.startsWith(`${{item.href}}/`);
          return (
            <Link
              key={{item.href}}
              href={{item.href}}
              className={{cn(
                "flex items-center gap-3 rounded-md px-3 py-2",
                "text-sm font-medium transition-colors",
                active ? "bg-blue-50 text-blue-700" : "text-gray-600 hover:bg-gray-50",
              )}}
            >
              {{item.label}}
            </Link>
          );
        }})}}
      </nav>{sign_out_block}
    </aside>
  );
}}
'''
    return GeneratedFile(
        path="frontend/src/components/AppSidebar.tsx",
        content=content,
        provenance=f"domain/{ctx.ir.domain}",
    )


def _nav_label(view: EntityView) -> str:
    return view.page.title or _label(view.page.name)

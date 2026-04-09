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
    has_kanban = any(v.get("type") == "kanban" for v in page.views) and entity.state_machine is not None
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

    table_render = f'<{cls}Table items={{items}} basePath="/{route_name}" onRefresh={{loadData}} />' if has_table else ""
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

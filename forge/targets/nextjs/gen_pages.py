"""Generate Next.js App Router pages from PageIR.

Every page here owns the states a network call can be in. The previous list
page did `const data = await tickets.list()` with no `catch`, so a failed fetch
left `items` empty and rendered the same view an empty collection does; and the
detail page returned "Loading..." forever when the fetch rejected. A generated
app has to say what went wrong, because nobody is going to open the console.

Pagination is keyset (CODEGEN_CONTRACT §7): there is no page count and no
total, so the list views append with "Load more" rather than paging.

Which pages and controls exist follows from the route contract's endpoints —
a Delete button is only rendered when a DELETE endpoint exists, because the
api client only has `remove` when one does.
"""

from __future__ import annotations

from forge.targets.base import GeneratedFile, provenance_header
from forge.targets.nextjs.context import EntityView, FrontendContext


def generate_pages(ctx: FrontendContext) -> list[GeneratedFile]:
    """Generate list, detail, create and edit pages for each page contract."""
    files: list[GeneratedFile] = []
    for view in ctx.views:
        files.append(_list_page(view))
        if "get" in view.methods:
            files.append(_detail_page(view))
            if "update" in view.methods:
                files.append(_edit_page(view))
        if "create" in view.methods:
            files.append(_create_page(view))
    return files


def _title(view: EntityView) -> str:
    return view.page.title or view.component


def _header(view: EntityView, description: str) -> str:
    return provenance_header("typescript", view.page.fqn, description).rstrip()


def _list_page(view: EntityView) -> GeneratedFile:
    cls = view.component
    api = view.api
    has_table = any(v.get("type") == "table" for v in view.page.views) or not view.page.views
    has_kanban = (
        any(v.get("type") == "kanban" for v in view.page.views)
        and view.entity.state_machine is not None
        and "transition" in view.methods
    )
    if not has_kanban:
        has_table = True
    default_view = (
        "kanban"
        if any(v.get("type") == "kanban" and v.get("default") for v in view.page.views)
        and has_kanban
        else "table"
    )
    can_delete = "remove" in view.methods
    can_create = "create" in view.methods

    imports = [
        '"use client";',
        'import { useState } from "react";',
    ]
    if can_create:
        imports.append('import { useRouter } from "next/navigation";')
    imports.append(f'import {{ {api}, errorMessage }} from "@/lib/api";')
    imports.append('import { useCursorList } from "@/lib/pagination";')
    imports.append('import { Button } from "@/components/ui/button";')
    imports.append(
        "import { EmptyState, ErrorState, InlineError, LoadingState }"
        ' from "@/components/ui/states";'
    )
    if has_table:
        imports.append(f'import {{ {cls}Table }} from "@/components/{cls}Table";')
    if has_kanban:
        imports.append(f'import {{ {cls}Kanban }} from "@/components/{cls}Kanban";')
    imports.append(f'import type {{ {cls} }} from "@/lib/types";')
    imports_src = "\n".join(imports)

    router_src = "  const router = useRouter();\n" if can_create else ""
    view_state_src = (
        '  const [view, setView] = useState<"table" | "kanban">'
        f'("{default_view}");\n'
        if has_table and has_kanban
        else ""
    )

    delete_handler = ""
    if can_delete:
        delete_handler = f'''
  async function handleDelete(id: string) {{
    if (!window.confirm("Delete this {view.entity.name}? This cannot be undone.")) return;
    setActionError(null);
    try {{
      await {api}.remove(id);
      list.reload();
    }} catch (cause) {{
      setActionError(errorMessage(cause));
    }}
  }}
'''

    transition_handler = ""
    if has_kanban:
        transition_handler = f'''
  async function handleTransition(id: string, newState: string) {{
    setActionError(null);
    try {{
      await {api}.transition(id, newState);
      list.reload();
    }} catch (cause) {{
      // A rejected transition used to reload and look like nothing happened.
      // The server distinguishes "no such record", "not a legal transition"
      // and "a guard is unsatisfied"; all three reach the user now.
      setActionError(errorMessage(cause));
    }}
  }}
'''

    toggle_src = ""
    if has_table and has_kanban:
        toggle_src = '''
          <Button
            variant={view === "table" ? "default" : "outline"}
            size="sm"
            onClick={() => setView("table")}
          >
            Table
          </Button>
          <Button
            variant={view === "kanban" ? "default" : "outline"}
            size="sm"
            onClick={() => setView("kanban")}
          >
            Kanban
          </Button>'''

    create_button = ""
    if can_create:
        create_button = f'''
          <Button onClick={{() => router.push("{view.url}/new")}}>Create</Button>'''

    table_src = (
        f'<{cls}Table items={{list.items}} basePath="{view.url}"'
        + (" onDelete={handleDelete}" if can_delete else "")
        + " />"
    )
    kanban_src = (
        f'<{cls}Kanban items={{list.items}} basePath="{view.url}" '
        "onTransition={handleTransition} />"
    )

    if has_table and has_kanban:
        collection_src = f'{{view === "table" ? {table_src} : {kanban_src}}}'
    elif has_kanban:
        collection_src = kanban_src
    else:
        collection_src = table_src

    content = f'''{_header(view, f"List view for {view.page.fqn}")}

{imports_src}

export default function {cls}ListPage() {{
{router_src}  // `{api}.list` is a stable module-level binding, so the hook does not
  // re-subscribe on every render.
  const list = useCursorList<{cls}>({api}.list);
  const [actionError, setActionError] = useState<string | null>(null);
{view_state_src}{delete_handler}{transition_handler}
  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">{_title(view)}</h1>
        <div className="flex items-center gap-2">{toggle_src}{create_button}
        </div>
      </div>

      {{actionError !== null && (
        <div className="mb-4">
          <InlineError message={{actionError}} />
        </div>
      )}}

      {{list.loading ? (
        <LoadingState />
      ) : list.error !== null ? (
        <ErrorState message={{list.error}} onRetry={{list.reload}} />
      ) : list.items.length === 0 ? (
        <EmptyState label="Nothing here yet." />
      ) : (
        <>
          {collection_src}
          {{list.hasMore && (
            <div className="mt-6 flex justify-center">
              <Button variant="outline" onClick={{list.loadMore}} disabled={{list.loadingMore}}>
                {{list.loadingMore ? "Loading..." : "Load more"}}
              </Button>
            </div>
          )}}
        </>
      )}}
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/app/{view.app_dir}/page.tsx",
        content=content,
        provenance=view.page.fqn,
    )


def _detail_page(view: EntityView) -> GeneratedFile:
    cls = view.component
    api = view.api
    can_delete = "remove" in view.methods
    can_edit = "update" in view.methods

    heading_field = next(
        (
            f.name
            for f in view.entity.fields
            if f.type in ("string", "text") and not f.computed
        ),
        "id",
    )

    delete_handler = ""
    delete_button = ""
    if can_delete:
        delete_handler = f'''
  async function handleDelete() {{
    if (!window.confirm("Delete this {view.entity.name}? This cannot be undone.")) return;
    setActionError(null);
    try {{
      await {api}.remove(id);
      router.push("{view.url}");
    }} catch (cause) {{
      setActionError(errorMessage(cause));
    }}
  }}
'''
        delete_button = '''
          <Button variant="destructive" onClick={handleDelete}>
            Delete
          </Button>'''

    edit_button = ""
    if can_edit:
        edit_button = f'''
          <Button
            variant="outline"
            onClick={{() => router.push(`{view.url}/${{encodeURIComponent(id)}}/edit`)}}
          >
            Edit
          </Button>'''

    content = f'''{_header(view, f"Detail view for {view.page.fqn}")}

"use client";
import {{ useEffect, useState }} from "react";
import {{ useParams, useRouter }} from "next/navigation";

import {{ {api}, errorMessage }} from "@/lib/api";
import {{ {cls}Detail }} from "@/components/{cls}Detail";
import {{ Button }} from "@/components/ui/button";
import {{ ErrorState, InlineError, LoadingState }} from "@/components/ui/states";
import type {{ {cls} }} from "@/lib/types";

export default function {cls}DetailPage() {{
  const params = useParams();
  const router = useRouter();
  const id = Array.isArray(params.id) ? params.id[0] : String(params.id ?? "");

  const [data, setData] = useState<{cls} | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {{
    let live = true;
    setLoading(true);
    setError(null);
    {api}
      .get(id)
      .then((record) => {{
        if (live) setData(record);
      }})
      .catch((cause) => {{
        // Without this the page sat on "Loading..." forever.
        if (live) setError(errorMessage(cause));
      }})
      .finally(() => {{
        if (live) setLoading(false);
      }});
    return () => {{
      live = false;
    }};
  }}, [id]);
{delete_handler}
  if (loading) return <LoadingState />;
  if (error !== null) return <ErrorState message={{error}} />;
  if (data === null) return <ErrorState message="That record could not be loaded." />;

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">
          {{data.{heading_field} == null ? String(data.id ?? "") : String(data.{heading_field})}}
        </h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={{() => router.push("{view.url}")}}>
            Back
          </Button>{edit_button}{delete_button}
        </div>
      </div>

      {{actionError !== null && (
        <div className="mb-4">
          <InlineError message={{actionError}} />
        </div>
      )}}

      <{cls}Detail data={{data}} />
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/app/{view.app_dir}/[id]/page.tsx",
        content=content,
        provenance=view.page.fqn,
    )


def _create_page(view: EntityView) -> GeneratedFile:
    cls = view.component
    api = view.api

    content = f'''{_header(view, f"Create form for {view.page.fqn}")}

"use client";
import {{ useState }} from "react";
import {{ useRouter }} from "next/navigation";

import {{ {api}, errorMessage }} from "@/lib/api";
import {{ {cls}Form }} from "@/components/{cls}Form";
import {{ InlineError }} from "@/components/ui/states";

export default function Create{cls}Page() {{
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(values: Record<string, unknown>) {{
    setError(null);
    try {{
      const created = await {api}.create(values);
      router.push(`{view.url}/${{encodeURIComponent(String(created.id))}}`);
    }} catch (cause) {{
      // The form stays filled in so the values are not lost to a failed save.
      setError(errorMessage(cause));
    }}
  }}

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Create {cls}</h1>
      {{error !== null && (
        <div className="mb-4 max-w-lg">
          <InlineError message={{error}} />
        </div>
      )}}
      <{cls}Form onSubmit={{handleSubmit}} submitLabel="Create" />
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/app/{view.app_dir}/new/page.tsx",
        content=content,
        provenance=view.page.fqn,
    )


def _edit_page(view: EntityView) -> GeneratedFile:
    cls = view.component
    api = view.api

    content = f'''{_header(view, f"Edit form for {view.page.fqn}")}

"use client";
import {{ useEffect, useState }} from "react";
import {{ useParams, useRouter }} from "next/navigation";

import {{ {api}, errorMessage }} from "@/lib/api";
import {{ {cls}Form }} from "@/components/{cls}Form";
import {{ ErrorState, InlineError, LoadingState }} from "@/components/ui/states";
import type {{ {cls} }} from "@/lib/types";

export default function Edit{cls}Page() {{
  const params = useParams();
  const router = useRouter();
  const id = Array.isArray(params.id) ? params.id[0] : String(params.id ?? "");

  const [data, setData] = useState<{cls} | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {{
    let live = true;
    {api}
      .get(id)
      .then((record) => {{
        if (live) setData(record);
      }})
      .catch((cause) => {{
        if (live) setLoadError(errorMessage(cause));
      }});
    return () => {{
      live = false;
    }};
  }}, [id]);

  async function handleSubmit(values: Record<string, unknown>) {{
    setSaveError(null);
    try {{
      await {api}.update(id, values);
      router.push(`{view.url}/${{encodeURIComponent(id)}}`);
    }} catch (cause) {{
      setSaveError(errorMessage(cause));
    }}
  }}

  if (loadError !== null) return <ErrorState message={{loadError}} />;
  if (data === null) return <LoadingState />;

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Edit {cls}</h1>
      {{saveError !== null && (
        <div className="mb-4 max-w-lg">
          <InlineError message={{saveError}} />
        </div>
      )}}
      <{cls}Form data={{data}} onSubmit={{handleSubmit}} submitLabel="Save changes" />
    </div>
  );
}}
'''
    return GeneratedFile(
        path=f"frontend/src/app/{view.app_dir}/[id]/edit/page.tsx",
        content=content,
        provenance=view.page.fqn,
    )

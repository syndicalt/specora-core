"""Generate TypeScript API client from RouteIR."""
from __future__ import annotations

from forge.ir.model import DomainIR, RouteIR, EndpointIR
from forge.targets.base import GeneratedFile, provenance_header


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def generate_api_client(ir: DomainIR) -> GeneratedFile:
    """Generate frontend/src/lib/api.ts with a typed fetch client."""
    provenance = ", ".join(r.fqn for r in ir.routes)
    header = provenance_header("typescript", provenance, "API client from route contracts")
    lines = [
        header.rstrip(),
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
        provenance=provenance,
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

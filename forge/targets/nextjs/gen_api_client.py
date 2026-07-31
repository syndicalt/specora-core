"""Generate the typed TypeScript API client from RouteIR.

Three things this file is responsible for getting right, each of which it
previously got wrong:

  1. **Credentials.** The client sent no `Authorization` header at all, while
     the API generator puts `require_auth`/`require_role` on every endpoint of
     an auth-declaring domain. Frontend and backend were generated from the
     same contracts and could not talk to each other: every call was a 401,
     and the caller had no handler for one, so the pages rendered empty.

  2. **Pagination.** `list(limit, offset)` is gone. `CODEGEN_CONTRACT.md` §7
     freezes the repository on keyset pagination — `limit` + `cursor` in,
     `{items, next_cursor}` out — because `OFFSET n` degrades linearly with
     table size on the hottest endpoint. There is no `total`; a count is a
     full scan, which is the thing being removed.

  3. **Endpoint coverage.** Any endpoint that was not one of six hard-coded
     shapes produced an empty string and was dropped from the client without a
     word. A contract declaring `POST /{id}/archive` got a backend handler and
     no way to call it. Unrecognised endpoints now get a real method derived
     from their path.

Errors never reach the DOM verbatim. The generated API's own 500 handler
returns `{"error": "internal_server_error", "detail": str(exc)}` — a raw Python
exception string, which can carry a connection string or a fragment of SQL.
`ApiError.userMessage` is chosen from a fixed table by status and machine-
readable code; the server's text goes to the console for the developer only.
"""

from __future__ import annotations

import re

from forge.ir.model import EndpointIR, RouteIR
from forge.targets.base import GeneratedFile, GenerationError, provenance_header
from forge.targets.naming import camel_case, py_identifier
from forge.targets.nextjs.context import (
    LOGIN_ROUTE,
    PATH_PARAM,
    FrontendContext,
    endpoint_method_name,
)


def generate_api_client(ctx: FrontendContext) -> list[GeneratedFile]:
    """Generate the api client and the config module it reads its base URL from."""
    return [_generate_config(ctx), _generate_client(ctx)]


def _generate_config(ctx: FrontendContext) -> GeneratedFile:
    """Emit the shared runtime configuration.

    Kept in its own module so `api.ts` and `session.ts` can both read it: the
    api client imports the session for its credentials, so the session
    importing the api client back would close a cycle.
    """
    header = provenance_header(
        "typescript", f"domain/{ctx.ir.domain}", "Frontend runtime configuration"
    )
    body = f'''
/** Origin of the generated API. Compiled into the bundle at build time. */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Rows requested per page.
 *
 * Deliberately small. With keyset pagination the cost of a page is
 * independent of how deep into the table it sits, so fetching more up front
 * buys nothing but a slower first paint.
 */
export const PAGE_SIZE = 50;

/** Where an unauthenticated caller is sent. */
export const LOGIN_ROUTE = "{LOGIN_ROUTE}";
'''
    return GeneratedFile(
        path="frontend/src/lib/config.ts",
        content=header.rstrip() + "\n" + body,
        provenance=f"domain/{ctx.ir.domain}",
    )


def _generate_client(ctx: FrontendContext) -> GeneratedFile:
    ir = ctx.ir
    provenance = ", ".join(r.fqn for r in ir.routes)
    header = provenance_header(
        "typescript", provenance, "Typed API client from route contracts"
    )

    entity_types = _entity_type_imports(ctx)
    lines: list[str] = [header.rstrip(), ""]

    lines.append('import { API_BASE, PAGE_SIZE } from "./config";')
    if ctx.auth is not None:
        lines.append(
            'import { authorizationHeader, endSession, refreshSession } from "./session";'
        )
    if entity_types:
        names = ", ".join(sorted(entity_types))
        lines.append(f'import type {{ {names} }} from "./types";')

    lines.extend(_preamble(has_auth=ctx.auth is not None))

    for route in ir.routes:
        lines.extend(_route_object(ctx, route))

    return GeneratedFile(
        path="frontend/src/lib/api.ts",
        content="\n".join(lines) + "\n",
        provenance=provenance,
    )


def _entity_type_imports(ctx: FrontendContext) -> set[str]:
    """Interface names to import from `types.ts` — exactly the ones used."""
    names = set()
    for route in ctx.ir.routes:
        stem = ctx.component_for_entity(route.entity_fqn)
        if stem:
            names.add(stem)
    return names


def _preamble(*, has_auth: bool) -> list[str]:
    """The shared fetch machinery: page shape, error type, request wrapper."""
    unauthorized = (
        [
            "  if (res.status === 401) {",
            "    // Exactly one recovery attempt. An access token that expired mid-",
            "    // session is replaceable by spending the refresh token; anything",
            "    // else means the session is over and retrying would loop.",
            "    if (allowRetry && (await refreshSession())) {",
            "      return request<T>(path, init, false);",
            "    }",
            "    endSession();",
            "    throw new ApiError(",
            '      401,',
            '      "unauthenticated",',
            '      "Your session has ended. Please sign in again.",',
            "    );",
            "  }",
            "",
        ]
        if has_auth
        else [
            "  if (res.status === 401) {",
            "    throw new ApiError(",
            "      401,",
            '      "unauthenticated",',
            '      "This request needs credentials the app does not have.",',
            "    );",
            "  }",
            "",
        ]
    )

    auth_header = "        ...authorizationHeader()," if has_auth else None

    lines = [
        "",
        "/**",
        " * One page of a keyset-paginated collection.",
        " *",
        " * `next_cursor` is opaque and is echoed back verbatim to fetch the page",
        " * after this one. `null` means there is nothing after it. There is no",
        " * total: counting is a full table scan, which is why offset paging was",
        " * removed in the first place.",
        " */",
        "export interface ListPage<T> {",
        "  items: T[];",
        "  next_cursor: string | null;",
        "}",
        "",
        "/**",
        " * The body of a create or update.",
        " *",
        " * Wider than `Partial<T>` on purpose: a field marked `sensitive` in its",
        " * contract is write-only, so it is absent from the response interface `T`",
        " * while still being a legal thing to send.",
        " */",
        "export type WritePayload<T> = Partial<T> & Record<string, unknown>;",
        "",
        "export interface ListParams {",
        "  /** Rows to fetch. Defaults to PAGE_SIZE. */",
        "  limit?: number;",
        "  /** `next_cursor` from the previous page, or null/undefined for the first. */",
        "  cursor?: string | null;",
        "}",
        "",
        "/**",
        " * A failed API call.",
        " *",
        " * `userMessage` is safe to render. `message` and the server's own text",
        " * are not: the API's 500 handler returns the stringified Python",
        " * exception, which can contain a connection string or a query fragment.",
        " */",
        "export class ApiError extends Error {",
        "  readonly status: number;",
        "  readonly code: string;",
        "  readonly userMessage: string;",
        "",
        "  constructor(status: number, code: string, userMessage: string) {",
        "    super(`${status} ${code || \"error\"}`);",
        '    this.name = "ApiError";',
        "    this.status = status;",
        "    this.code = code;",
        "    this.userMessage = userMessage;",
        "  }",
        "}",
        "",
        "/** Turn any thrown value into something safe to put on screen. */",
        "export function errorMessage(err: unknown): string {",
        "  if (err instanceof ApiError) return err.userMessage;",
        '  return "Something went wrong. Please try again.";',
        "}",
        "",
        "const MESSAGE_BY_CODE: Record<string, string> = {",
        '  invalid_credentials: "That sign-in was not recognised.",',
        '  forbidden: "You do not have permission to do that.",',
        '  not_found: "That record no longer exists.",',
        '  invalid_transition: "That is not a valid next state for this record.",',
        '  guard_failed: "This record is missing information that change requires.",',
        "};",
        "",
        "const MESSAGE_BY_STATUS: Record<number, string> = {",
        '  400: "The server could not accept that request.",',
        '  403: "You do not have permission to do that.",',
        '  404: "That record no longer exists.",',
        '  409: "Someone else changed this record. Reload and try again.",',
        '  422: "Some of those values are not valid.",',
        '  429: "Too many requests. Wait a moment and try again.",',
        "};",
        "",
        "function serverCode(body: unknown): string {",
        '  if (!body || typeof body !== "object") return "";',
        "  const record = body as Record<string, unknown>;",
        '  if (typeof record.error === "string") return record.error;',
        "  // FastAPI wraps an HTTPException's detail; the generated handlers put",
        "  // their machine-readable code inside it.",
        "  const detail = record.detail;",
        '  if (detail && typeof detail === "object" && !Array.isArray(detail)) {',
        "    const nested = (detail as Record<string, unknown>).error;",
        '    if (typeof nested === "string") return nested;',
        "  }",
        '  return "";',
        "}",
        "",
        "async function readBody(res: Response): Promise<unknown> {",
        "  // A gateway or proxy failure answers with HTML, and an empty body is",
        "  // legal on several statuses. Neither should surface as a JSON parse",
        "  // error the user cannot act on.",
        "  const text = await res.text();",
        "  if (!text) return null;",
        "  try {",
        "    return JSON.parse(text) as unknown;",
        "  } catch {",
        "    return null;",
        "  }",
        "}",
        "",
        "function failure(status: number, body: unknown): ApiError {",
        "  const code = serverCode(body);",
        "  const userMessage =",
        "    MESSAGE_BY_CODE[code] ||",
        "    MESSAGE_BY_STATUS[status] ||",
        '    "The server had a problem handling that request. Try again shortly.";',
        "  // The raw payload is for whoever is debugging, never for the page.",
        '  console.error("API request failed", { status, code, body });',
        "  return new ApiError(status, code, userMessage);",
        "}",
        "",
        "async function request<T>(",
        "  path: string,",
        "  init?: RequestInit,",
        "  allowRetry = true,",
        "): Promise<T> {",
        "  let res: Response;",
        "  try {",
        "    res = await fetch(`${API_BASE}${path}`, {",
        "      ...init,",
        "      headers: {",
        '        "Content-Type": "application/json",',
    ]
    if auth_header:
        lines.append(auth_header)
    lines.extend(
        [
            "        ...(init?.headers ?? {}),",
            "      },",
            "    });",
            "  } catch (cause) {",
            "    // No status to map: DNS failure, offline, or a CORS rejection.",
            '    console.error("API request could not be sent", cause);',
            "    throw new ApiError(",
            "      0,",
            '      "network_error",',
            '      "Could not reach the server. Check your connection and try again.",',
            "    );",
            "  }",
            "",
        ]
    )
    lines.extend(unauthorized)
    lines.extend(
        [
            "  if (res.status === 204) return undefined as T;",
            "",
            "  const body = await readBody(res);",
            "  if (!res.ok) throw failure(res.status, body);",
            "  return body as T;",
            "}",
            "",
            "async function requestPage<T>(path: string): Promise<ListPage<T>> {",
            "  const page = await request<Partial<ListPage<T>>>(path);",
            "  // Normalised once here so no caller has to guard `.items.map`.",
            "  return {",
            "    items: Array.isArray(page?.items) ? page.items : [],",
            "    next_cursor: page?.next_cursor ?? null,",
            "  };",
            "}",
            "",
            "function listQuery(params?: ListParams): string {",
            "  const query = new URLSearchParams();",
            "  query.set(\"limit\", String(params?.limit ?? PAGE_SIZE));",
            "  // URLSearchParams percent-encodes the cursor. It is opaque and may",
            "  // legitimately contain '+' or '=', which raw interpolation corrupts.",
            '  if (params?.cursor) query.set("cursor", params.cursor);',
            "  return query.toString();",
            "}",
            "",
        ]
    )
    return lines


def _route_object(ctx: FrontendContext, route: RouteIR) -> list[str]:
    """Emit one `export const <binding> = { ... }` for a route contract."""
    binding = ctx.api_for_entity(route.entity_fqn) or py_identifier(route.name)
    model = ctx.component_for_entity(route.entity_fqn) or "Record<string, unknown>"
    base = (route.base_path or f"/{route.name}").rstrip("/")

    methods: dict[str, str] = {}
    for endpoint in route.endpoints:
        name, body = _endpoint_method(endpoint, base, model)
        if name in methods:
            raise GenerationError(
                f"{route.fqn}: endpoints {endpoint.method} {endpoint.path!r} and an "
                f"earlier one both compile to the client method {name!r}. One would "
                f"overwrite the other. Give them distinct paths."
            )
        methods[name] = body

    lines = [f"export const {binding} = {{"]
    for body in methods.values():
        lines.append(f"  {body}")
    lines.append("};")
    lines.append("")
    return lines


def _endpoint_method(endpoint: EndpointIR, base: str, model: str) -> tuple[str, str]:
    """Compile one endpoint into a named client method.

    The name comes from `context.endpoint_method_name` so that the page
    generator, which decides whether to render a Delete button, and this
    generator, which decides whether `remove` exists, cannot disagree.

    Returns:
        (method name, the full `name: (args) => body,` source line).
    """
    method = endpoint.method.upper()
    path = endpoint.path or "/"
    params = PATH_PARAM.findall(path)
    url = _template_url(base, path)
    name = endpoint_method_name(endpoint)

    if name == "list":
        return (
            name,
            f"list: (params?: ListParams): Promise<ListPage<{model}>> =>\n"
            f"    requestPage<{model}>(`{url}?${{listQuery(params)}}`),",
        )
    if name == "get":
        return (name, f"get: (id: string): Promise<{model}> => request<{model}>(`{url}`),")
    if name == "create":
        return (
            name,
            f"create: (data: WritePayload<{model}>): Promise<{model}> =>\n"
            f'    request<{model}>(`{url}`, {{ method: "POST", body: JSON.stringify(data) }}),',
        )
    if name == "update":
        return (
            name,
            f"update: (id: string, data: WritePayload<{model}>): Promise<{model}> =>\n"
            f'    request<{model}>(`{url}`, {{ method: "{method}", body: JSON.stringify(data) }}),',
        )
    if name == "remove":
        return (
            name,
            "remove: (id: string): Promise<void> =>\n"
            f'    request<void>(`{url}`, {{ method: "DELETE" }}),',
        )
    if name == "transition":
        return (
            name,
            f"transition: (id: string, state: string): Promise<{model}> =>\n"
            f"    request<{model}>(`{url}`, {{\n"
            f'      method: "{method}",\n'
            f"      body: JSON.stringify({{ state }}),\n"
            f"    }}),",
        )

    # Anything the six canonical shapes do not cover still gets a real method,
    # named from the path's static segments. Dropping it — the previous
    # behaviour — left a generated backend handler with no way to call it.
    return _custom_method(endpoint, name, method, params, url, model)


def _custom_method(
    endpoint: EndpointIR,
    name: str,
    method: str,
    params: list[str],
    url: str,
    model: str,
) -> tuple[str, str]:
    args = [f"{camel_case(py_identifier(p))}: string" for p in params]
    takes_body = method in ("POST", "PUT", "PATCH")
    if takes_body:
        args.append(f"data: WritePayload<{model}>")
    signature = ", ".join(args)

    options = [f'method: "{method}"']
    if takes_body:
        options.append("body: JSON.stringify(data)")
    options_src = "{ " + ", ".join(options) + " }"

    returns = "void" if endpoint.response_status == 204 else model
    return (
        name,
        f"{name}: ({signature}): Promise<{returns}> =>\n"
        f"    request<{returns}>(`{url}`, {options_src}),",
    )


def _template_url(base: str, path: str) -> str:
    """Build the JS template-literal URL, encoding every path parameter.

    Identifiers reach here from the browser's own URL. Interpolating one raw
    lets a crafted id add path segments or a query string to the request; every
    parameter goes through `encodeURIComponent`.
    """
    suffix = path if path != "/" else "/"

    def substitute(match: re.Match[str]) -> str:
        binding = camel_case(py_identifier(match.group(1)))
        return f"${{encodeURIComponent({binding})}}"

    return f"{base}{PATH_PARAM.sub(substitute, suffix)}"

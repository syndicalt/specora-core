"""Generate FastAPI route handlers that call repositories.

Handlers are emitted from an exact (method, path) dispatch table rather than
from substring tests on the path. Substring dispatch conflated every path
containing `{id}`, so a contract declaring both `GET /{id}` and
`GET /{id}/history` produced two identically-named handlers decorated with the
same route: Python kept the second definition, FastAPI served the first, and
the `/history` endpoint vanished from the application without a warning.

Any (method, path) shape outside the table raises `GenerationError`. The
previous fallback emitted a 200 stub whose function name was derived by
`path.replace("/", "_")` — for `/{id}/archive` that is
`post_order_{id}_archive`, not a legal identifier, so the module failed to
import and took the whole application down at deploy time.
"""
from __future__ import annotations

from forge.ir.model import DomainIR, EndpointIR, EntityIR, RouteIR
from forge.targets.base import GeneratedFile, GenerationError, provenance_header
from forge.targets.naming import (
    class_name,
    module_slug,
    pluralize,
    py_identifier,
    repo_accessor,
)

# Keyset page bounds. `limit` was previously an unbounded `int` default, so
# `?limit=999999999` was a single-request denial of service against the
# hottest endpoint in every generated app.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# The FastAPI path parameter name. Contracts write `{id}`; `id` shadows the
# builtin inside the handler body, so it is renamed on the way out.
PATH_PARAM = "record_id"

# Repository transition failures carry a stable code so that "no such record",
# "that transition is not in the machine", and "a guard rejected it" reach the
# client as distinct statuses instead of one indistinguishable 422.
TRANSITION_ERROR_STATUS = {
    "not_found": 404,
    "invalid_transition": 409,
    "guard_failed": 422,
}

# Generated modules are linted by the app's own gate, which inherits this
# repo's line length.
MAX_LINE = 100

SUPPORTED_SHAPES = (
    "GET /", "POST /", "GET /{id}", "PATCH /{id}", "DELETE /{id}", "PUT /{id}/state",
)


def generate_routes(ir: DomainIR) -> list[GeneratedFile]:
    """Generate one route module per Route contract."""
    entity_map = {e.fqn: e for e in ir.entities}
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)

    files = []
    for route in ir.routes:
        entity = entity_map.get(route.entity_fqn)
        if entity is None:
            raise GenerationError(
                f"Route {route.fqn!r} manages entity {route.entity_fqn!r}, which is "
                f"not in the compiled IR. The generated module would import models "
                f"and a repository that do not exist."
            )
        files.append(_generate_route(ir, route, entity, auth_infra))
    return files


def _canonical_path(path: str) -> str:
    """Normalize a declared endpoint path to its dispatch key.

    `/{id}` and `/{id}/` are the same endpoint and must not produce two
    handlers.
    """
    p = path.strip()
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") or "/"


def _auth_dependency(endpoint: EndpointIR, auth_infra) -> tuple[str | None, str | None]:
    """Return the handler's auth parameter and the middleware name it needs."""
    if not auth_infra:
        return None, None
    if endpoint.roles:
        roles = ", ".join(f'"{r}"' for r in endpoint.roles)
        return f"user = Depends(require_role({roles}))", "require_role"
    return "user = Depends(require_auth)", "require_auth"


def _def_lines(name: str, params: list[str]) -> list[str]:
    """Render an `async def` with one parameter per line.

    A repository dependency plus a role check plus a body model does not fit on
    one line for any realistic entity name, and the generated app is linted.
    """
    return [f"async def {name}(", *(f"    {p}," for p in params), "):"]


class _RouteModule:
    """Accumulates one route module's handlers and the imports they require.

    Imports are tracked as handlers are emitted so the module ends up with
    exactly the names it uses — a fixed preamble fails the generated app's own
    lint gate.
    """

    def __init__(self, ir: DomainIR, route: RouteIR, entity: EntityIR, auth_infra) -> None:
        self.route = route
        self.entity = entity
        self.auth_infra = auth_infra
        self.cls = class_name(entity.name, entity.domain, multi_domain=ir.multi_domain)
        self.slug = module_slug(entity.name, entity.domain, multi_domain=ir.multi_domain)
        self.repo_getter = repo_accessor(entity.name, entity.domain, multi_domain=ir.multi_domain)
        self.base_path = route.base_path or f"/{pluralize(entity.name)}"

        self.repo_dep = f"repo: {self.cls}Repository = Depends({self.repo_getter})"
        self.sensitive = sorted(f.name for f in entity.fields if f.sensitive)
        self.model_imports: set[str] = set()
        self.auth_imports: set[str] = set()
        self.fastapi_imports: set[str] = {"APIRouter", "Depends", "HTTPException"}
        self.stdlib_imports: set[str] = set()
        self.handlers: dict[str, list[str]] = {}

    def public(self, expr: str) -> str:
        """Wrap a record expression in the module's write-only-column filter.

        Entities with no `sensitive` field get the bare expression, so their
        generated output is unchanged.
        """
        return f"_public({expr})" if self.sensitive else expr

    def _public_helper(self) -> list[str]:
        """Module preamble that strips write-only columns from a record.

        The response models already omit these fields, so this is the second of
        two independent controls on a credential column. It is here because the
        handler hands the repository's row to FastAPI as a plain dict: the
        moment a `response_model=` is dropped or a handler starts building its
        own payload, the model's omission stops protecting anything, and the
        column this guards is a password hash.
        """
        if not self.sensitive:
            return []
        names = ", ".join(f'"{n}"' for n in self.sensitive)
        return [
            f"_SENSITIVE_FIELDS = frozenset({{{names}}})",
            "",
            "",
            "def _public(record: dict) -> dict:",
            '    """Drop write-only columns before a record leaves the process."""',
            "    return {k: v for k, v in record.items() if k not in _SENSITIVE_FIELDS}",
            "",
            "",
        ]

    def params(self, auth_param: str | None, *leading: str) -> list[str]:
        """Handler parameters: the endpoint's own, then the repo, then auth."""
        return [*leading, self.repo_dep, *([auth_param] if auth_param else [])]

    def add(self, name: str, lines: list[str]) -> None:
        if name in self.handlers:
            raise GenerationError(
                f"Route {self.route.fqn!r} declares two endpoints that both compile "
                f"to the handler {name!r}. One would overwrite the other and the "
                f"endpoint would disappear from the application."
            )
        self.handlers[name] = lines

    def render(self) -> str:
        lines = [
            provenance_header("python", self.route.fqn, f"API routes for {self.route.name}"),
            "from __future__ import annotations",
            "",
        ]
        if self.stdlib_imports:
            plain = sorted(i for i in self.stdlib_imports if i.startswith("import "))
            frm = sorted(i for i in self.stdlib_imports if not i.startswith("import "))
            lines.extend(plain + frm)
            lines.append("")
        lines.append(f"from fastapi import {', '.join(sorted(self.fastapi_imports))}")
        lines.append("")
        # A route set of only DELETE endpoints references no model at all, and
        # `from backend.models import` with an empty name list is a SyntaxError.
        if self.model_imports:
            names = ", ".join(sorted(self.model_imports))
            single = f"from backend.models import {names}"
            if len(single) <= MAX_LINE:
                lines.append(single)
            else:
                lines.append("from backend.models import (")
                lines.extend(f"    {n}," for n in sorted(self.model_imports))
                lines.append(")")
        lines.append(
            f"from backend.repositories.base import {self.cls}Repository, {self.repo_getter}"
        )
        if self.auth_imports:
            lines.append(
                f"from backend.auth.middleware import {', '.join(sorted(self.auth_imports))}"
            )
        lines.extend([
            "",
            f'router = APIRouter(prefix="{self.base_path}", tags=["{self.entity.name}"])',
            "",
            "",
        ])
        lines.extend(self._public_helper())
        for handler in self.handlers.values():
            lines.extend(handler)
            lines.append("")
        return "\n".join(lines)


def _generate_route(ir: DomainIR, route: RouteIR, entity: EntityIR, auth_infra) -> GeneratedFile:
    module = _RouteModule(ir, route, entity, auth_infra)

    for endpoint in route.endpoints:
        _emit_endpoint(module, endpoint)

    return GeneratedFile(
        path=f"backend/routes_{module.slug}.py",
        content=module.render(),
        provenance=route.fqn,
    )


def _emit_endpoint(module: _RouteModule, endpoint: EndpointIR) -> None:
    key = (endpoint.method.lower(), _canonical_path(endpoint.path))
    emitter = _EMITTERS.get(key)
    if emitter is None:
        raise GenerationError(
            f"Route {module.route.fqn!r} declares {endpoint.method.upper()} "
            f"{endpoint.path!r}, which this generator cannot turn into a handler. "
            f"Supported shapes: {', '.join(SUPPORTED_SHAPES)}. Express the "
            f"behaviour as one of those, or extend the fastapi_prod generator — "
            f"emitting a stub here would ship an endpoint that answers 200 "
            f"without doing anything."
        )
    emitter(module, endpoint)


# ── Handler emitters ────────────────────────────────────────────────────────


def _emit_list(module: _RouteModule, endpoint: EndpointIR) -> None:
    name = py_identifier(f"list_{pluralize(module.slug)}")
    auth_param, auth_import = _auth_dependency(endpoint, module.auth_infra)
    module.fastapi_imports.add("Query")
    module.model_imports.update({f"{module.cls}Page", f"{module.cls}Response"})
    if auth_import:
        module.auth_imports.add(auth_import)

    params = module.params(
        auth_param,
        f"limit: int = Query({DEFAULT_PAGE_SIZE}, ge=1, le={MAX_PAGE_SIZE})",
        "cursor: str | None = Query(None)",
    )
    module.add(name, [
        f'@router.get("/", response_model={module.cls}Page)',
        *_def_lines(name, params),
        f'    """List {module.entity.name} records, newest first."""',
        "    page = await repo.list(limit=limit, cursor=cursor)",
        f'    return {{"items": [{module.public("i")} for i in page.items], '
        '"next_cursor": page.next_cursor}'
        if module.sensitive
        else '    return {"items": page.items, "next_cursor": page.next_cursor}',
    ])


def _emit_create(module: _RouteModule, endpoint: EndpointIR) -> None:
    name = py_identifier(f"create_{module.slug}")
    auth_param, auth_import = _auth_dependency(endpoint, module.auth_infra)
    status = endpoint.response_status or 201
    module.model_imports.update({f"{module.cls}Create", f"{module.cls}Response"})
    if auth_import:
        module.auth_imports.add(auth_import)

    lines = [
        f'@router.post("/", status_code={status}, response_model={module.cls}Response)',
        *_def_lines(name, module.params(auth_param, f"body: {module.cls}Create")),
        f'    """Create a {module.entity.name}."""',
        # `exclude_unset`, not `exclude_none`: an explicit null is a value the
        # caller chose and must reach the repository.
        "    data = body.model_dump(exclude_unset=True)",
    ]
    for field_name, expr in endpoint.auto_fields.items():
        lowered = expr.lower()
        if "uuid" in lowered:
            module.stdlib_imports.add("import uuid")
            lines.append(f'    data["{field_name}"] = str(uuid.uuid4())')
        elif "now" in lowered:
            module.stdlib_imports.add("from datetime import datetime, timezone")
            # A datetime object, not an ISO string: the column is TIMESTAMPTZ
            # and asyncpg will not encode a str into it.
            lines.append(f'    data["{field_name}"] = datetime.now(timezone.utc)')

    self_link = '{"self": f"' + module.base_path + "/{record['id']}\"}"
    lines.extend([
        "    record = await repo.create(data)",
        # The memory adapter returns the stored object itself, so mutating the
        # return value writes the link back into the store, where it reappears
        # on every later read.
        "    return {**" + module.public("record") + ', "_links": ' + self_link + "}",
    ])
    module.add(name, lines)


def _emit_get(module: _RouteModule, endpoint: EndpointIR) -> None:
    name = py_identifier(f"get_{module.slug}")
    auth_param, auth_import = _auth_dependency(endpoint, module.auth_infra)
    module.model_imports.add(f"{module.cls}Response")
    if auth_import:
        module.auth_imports.add(auth_import)

    module.add(name, [
        f'@router.get("/{{{PATH_PARAM}}}", response_model={module.cls}Response)',
        *_def_lines(name, module.params(auth_param, f"{PATH_PARAM}: str")),
        f'    """Fetch a {module.entity.name} by ID."""',
        f"    record = await repo.get({PATH_PARAM})",
        "    if record is None:",
        '        raise HTTPException(404, detail={"error": "not_found"})',
        f"    return {module.public('record')}",
    ])


def _emit_update(module: _RouteModule, endpoint: EndpointIR) -> None:
    name = py_identifier(f"update_{module.slug}")
    auth_param, auth_import = _auth_dependency(endpoint, module.auth_infra)
    module.model_imports.update({f"{module.cls}Update", f"{module.cls}Response"})
    if auth_import:
        module.auth_imports.add(auth_import)

    module.add(name, [
        f'@router.patch("/{{{PATH_PARAM}}}", response_model={module.cls}Response)',
        *_def_lines(
            name,
            module.params(auth_param, f"{PATH_PARAM}: str", f"body: {module.cls}Update"),
        ),
        f'    """Partially update a {module.entity.name}."""',
        # `exclude_unset` is what makes PATCH a partial update: every field on
        # the model defaults to None, so `exclude_none` would erase the
        # difference between "not mentioned" and "set to null" and a nullable
        # field could never be cleared.
        "    data = body.model_dump(exclude_unset=True)",
        "    if not data:",
        # An empty patch changes nothing; it must still answer with the current
        # representation rather than send an empty SET clause to the adapter.
        f"        record = await repo.get({PATH_PARAM})",
        "    else:",
        f"        record = await repo.update({PATH_PARAM}, data)",
        "    if record is None:",
        '        raise HTTPException(404, detail={"error": "not_found"})',
        f"    return {module.public('record')}",
    ])


def _emit_delete(module: _RouteModule, endpoint: EndpointIR) -> None:
    name = py_identifier(f"delete_{module.slug}")
    auth_param, auth_import = _auth_dependency(endpoint, module.auth_infra)
    if auth_import:
        module.auth_imports.add(auth_import)

    module.add(name, [
        f'@router.delete("/{{{PATH_PARAM}}}", status_code=204)',
        *_def_lines(name, module.params(auth_param, f"{PATH_PARAM}: str")),
        f'    """Delete a {module.entity.name}."""',
        f"    deleted = await repo.delete({PATH_PARAM})",
        "    if not deleted:",
        '        raise HTTPException(404, detail={"error": "not_found"})',
        "    return None",
    ])


def _emit_transition(module: _RouteModule, endpoint: EndpointIR) -> None:
    if module.entity.state_machine is None:
        raise GenerationError(
            f"Route {module.route.fqn!r} declares PUT {endpoint.path!r}, but entity "
            f"{module.entity.fqn!r} binds no workflow. Its repository has no "
            f"transition() method, so the handler would call something that does "
            f"not exist. Bind a workflow contract or drop the endpoint."
        )

    name = py_identifier(f"transition_{module.slug}")
    auth_param, auth_import = _auth_dependency(endpoint, module.auth_infra)
    module.model_imports.update({f"{module.cls}StateChange", f"{module.cls}Response"})
    if auth_import:
        module.auth_imports.add(auth_import)

    status_map = [
        "# A refused transition has three distinct causes and the caller has to",
        "# tell them apart: the record is gone, the machine has no such edge, or",
        "# a guard rejected the move.",
        "_TRANSITION_STATUS = {",
        *(f'    "{code}": {status},' for code, status in TRANSITION_ERROR_STATUS.items()),
        "}",
        "",
        "",
    ]
    lines = [
        *status_map,
        f'@router.put("/{{{PATH_PARAM}}}/state", response_model={module.cls}Response)',
        *_def_lines(
            name,
            module.params(auth_param, f"{PATH_PARAM}: str", f"body: {module.cls}StateChange"),
        ),
        f'    """Move a {module.entity.name} to a new lifecycle state."""',
        # The only write path to `state`. Create and update models exclude the
        # field, so the machine's transitions and guards cannot be bypassed.
        f"    result = await repo.transition({PATH_PARAM}, body.state)",
        "    if result.error is not None:",
        "        status = _TRANSITION_STATUS.get(result.error, 422)",
        '        raise HTTPException(status, detail={"error": result.error})',
        f"    return {module.public('result.record')}",
    ]
    module.add(name, lines)


_EMITTERS = {
    ("get", "/"): _emit_list,
    ("post", "/"): _emit_create,
    ("get", "/{id}"): _emit_get,
    ("patch", "/{id}"): _emit_update,
    ("delete", "/{id}"): _emit_delete,
    ("put", "/{id}/state"): _emit_transition,
}

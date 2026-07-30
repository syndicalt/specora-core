"""Generate the black-box pytest suite that ships inside every generated app.

Two kinds of file come out of here:

    backend/tests/conftest.py       TestClient, store isolation, auth headers
    backend/tests/test_<slug>.py    one per Route contract

Every generated test is behavioural: it makes a request against a live
TestClient and asserts the status and the body the application actually
returns. None of them assert over generated source text — a substring check
passes just as happily against an application that cannot boot.

Running the generated suite (pytest lives in `requirements-dev.txt`, which the
runtime image deliberately does not install — see gen_docker):

    pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest backend/tests

What is deliberately *not* emitted, and why:

  * `state` never appears in a create or update payload. gen_models excludes it
    from the request models and those models forbid unknown keys, so a payload
    carrying it is a 422 — which is asserted directly instead, because writing
    `state` through the ordinary update endpoint was the bypass that made every
    transition rule in every workflow contract advisory.
  * A guard-failure test is emitted only when the guard can be made to fail
    through the API: every field it requires must be omittable on create, and
    the record must be drivable to the guard's source state without crossing
    another guard that needs one of those same fields. A guard over a required
    field can never fail, and asserting that it does would assert the opposite
    of the contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.ir.model import (
    DomainIR,
    EndpointIR,
    EntityIR,
    FieldIR,
    GuardIR,
    InfraIR,
    RouteIR,
    StateMachineIR,
)
from forge.targets.base import GeneratedFile, GenerationError, provenance_header

# The exact filter gen_models applies when it builds <Cls>Create. A payload
# assembled from a different rule is a payload the model rejects, so this is
# imported rather than restated: a rename over there must break generation
# here, loudly, instead of emitting a suite that 422s on every create.
from forge.targets.fastapi_prod.gen_models import _writable_fields
from forge.targets.naming import module_slug, pluralize, py_identifier

# Generated test modules are read by humans debugging a failing deployment and
# are held to the same line budget as the rest of the generated app.
MAX_LINE = 100

# backend.config refuses to boot on the shipped placeholder or on anything
# shorter than 32 characters, so the suite's default has to clear both bars.
TEST_AUTH_SECRET = "specora-test-secret-not-for-any-deployment"

# Written into a write-only column and then searched for in every response
# body. Distinctive enough that finding it anywhere is proof of a disclosure.
SENSITIVE_SENTINEL = "specora-write-only-sentinel-8f21"

# Not a state any machine declares. The repository must answer with
# `invalid_transition` rather than raise.
UNKNOWN_STATE = "no-such-state"

_TYPE_DEFAULTS: dict[str, str] = {
    "string": '"test"',
    "integer": "1",
    "number": "1.0",
    "boolean": "True",
    "text": '"test text"',
    "uuid": '"00000000-0000-0000-0000-000000000001"',
    "email": '"test@example.com"',
    "datetime": '"2024-01-01T00:00:00Z"',
    "date": '"2024-01-01"',
    "array": "[]",
    "object": "{}",
}

_TYPE_ALTERNATES: dict[str, str] = {
    "string": '"updated"',
    "text": '"updated text"',
    "boolean": "False",
}

# Types whose value survives a write/read round trip unchanged, so a generated
# update test can assert the new value came back. datetime and uuid are absent
# on purpose: pydantic re-serialises them, and an assertion that compares the
# submitted string to the rendered one fails on formatting rather than on
# behaviour.
_ROUND_TRIP_TYPES = frozenset({"string", "text", "integer", "number", "boolean"})


# =============================================================================
# Payload construction
# =============================================================================


def _default_value(field: FieldIR) -> str:
    """A Python literal, as source text, that satisfies this field."""
    if field.enum_values:
        return repr(field.enum_values[0])
    if field.type == "integer":
        minimum = field.constraints.get("min")
        return str(minimum) if minimum is not None else "1"
    if field.type == "number":
        minimum = field.constraints.get("min")
        return str(float(minimum)) if minimum is not None else "1.0"
    return _TYPE_DEFAULTS.get(field.type, '"test"')


def _alternate_value(field: FieldIR) -> str | None:
    """A second literal, distinct from `_default_value`, or None if there isn't one.

    An update test that writes back the value the record already holds proves
    nothing, so a field with no distinguishable second value is not used.
    """
    if field.enum_values:
        return repr(field.enum_values[1]) if len(field.enum_values) > 1 else None
    if field.type in ("integer", "number"):
        maximum = field.constraints.get("max")
        base = float(_default_value(field))
        if maximum is not None and base + 1 > float(maximum):
            return None
        return str(int(base) + 1) if field.type == "integer" else str(base + 1.0)
    return _TYPE_ALTERNATES.get(field.type)


def _payload_fields(entity: EntityIR) -> list[FieldIR]:
    """Fields the generated create payload sets.

    `id` is dropped even where it is writable: the route contract's
    `auto_fields` assigns it, and a client-supplied id would fight with that.
    """
    return [f for f in _writable_fields(entity) if f.name != "id"]


def _valid_payload_code(entity: EntityIR) -> str:
    """A dict literal, as source text, that the entity's create model accepts."""
    fields = _payload_fields(entity)
    if not fields:
        return "{}"
    pairs = [f'    "{f.name}": {_default_value(f)}' for f in fields]
    return "{\n" + ",\n".join(pairs) + ",\n}"


def _required_payload_fields(entity: EntityIR) -> list[FieldIR]:
    return [f for f in _payload_fields(entity) if f.required]


def _updatable_field(entity: EntityIR) -> FieldIR | None:
    """A field whose update can be asserted by comparing the response back."""
    for candidate in _payload_fields(entity):
        if candidate.type not in _ROUND_TRIP_TYPES and not candidate.enum_values:
            continue
        if _alternate_value(candidate) is not None:
            return candidate
    return None


def _missing_id(entity: EntityIR) -> str:
    """An id of the right shape that no record will ever have.

    When the id column is a UUID the route types its path parameter as one, so
    `/things/nonexistent-id` is a 422 from the boundary and never reaches the
    handler that would have answered 404.
    """
    id_field = next((f for f in entity.fields if f.name == "id"), None)
    if id_field is not None and id_field.type == "uuid":
        return "00000000-0000-0000-0000-0000000000ff"
    return "no-such-record"


def _id_is_uuid(entity: EntityIR) -> bool:
    id_field = next((f for f in entity.fields if f.name == "id"), None)
    return id_field is not None and id_field.type == "uuid"


def _sensitive_fields(entity: EntityIR) -> list[FieldIR]:
    return [f for f in _payload_fields(entity) if f.sensitive]


# =============================================================================
# Authorization
# =============================================================================


class _AuthPlan:
    """Which role each endpoint's tests present, and which one must be refused.

    Two independent controls decide who may call an endpoint, and a request has
    to satisfy both: the route contract's per-endpoint `roles` (a `require_role`
    dependency) and the auth contract's `protected_routes` (an HTTP middleware
    ahead of every handler). The role a test presents therefore has to be in
    the intersection, and the role a 403 test presents has to be outside it.
    """

    def __init__(self, route: RouteIR, base_path: str, infra: InfraIR | None) -> None:
        self.infra = infra
        self.route = route
        self.base_path = base_path.rstrip("/") or "/"
        declared = [str(r) for r in (infra.config.get("roles") or [])] if infra else []
        # A contract may declare auth without naming roles; every endpoint is
        # then merely authenticated, and any role in a valid token passes.
        self.roles = declared or (["admin"] if infra else [])

    @property
    def enabled(self) -> bool:
        return self.infra is not None

    @property
    def test_args(self) -> str:
        """The fixture parameters a generated test function declares."""
        return "client, auth_headers" if self.enabled else "client"

    def _rule_roles(self, method: str) -> list[str] | None:
        """Roles `protected_routes` demands for this method under this base path."""
        if self.infra is None:
            return None
        for rule in self.infra.config.get("protected_routes") or []:
            if not isinstance(rule, dict):
                continue
            prefix = str(rule.get("path", "")).rstrip("/")
            if not prefix:
                continue
            if self.base_path != prefix and not self.base_path.startswith(prefix + "/"):
                continue
            methods = {str(m).upper() for m in rule.get("methods", [])}
            if methods and method.upper() not in methods:
                continue
            return [str(r) for r in rule.get("roles", [])]
        return None

    def _allowed(self, endpoint: EndpointIR) -> list[str]:
        allowed = [str(r) for r in endpoint.roles] if endpoint.roles else None
        rule = self._rule_roles(endpoint.method)
        if rule is not None:
            allowed = [r for r in allowed if r in rule] if allowed else rule
        if allowed is None:
            allowed = list(self.roles)
        if not allowed:
            raise GenerationError(
                f"Route {self.route.fqn!r}: {endpoint.method.upper()} "
                f"{endpoint.path!r} is reachable by no role at all. The route "
                f"contract permits {sorted(endpoint.roles) or 'any role'} and "
                f"infra protected_routes requires {sorted(rule or [])}; the two "
                f"do not overlap, so every request to it is a 403."
            )
        # Contract order, so the same endpoint always picks the same role.
        return sorted(set(allowed), key=lambda r: self.roles.index(r) if r in self.roles else -1)

    def role(self, endpoint: EndpointIR) -> str | None:
        """A role permitted to call this endpoint, or None when auth is off."""
        return self._allowed(endpoint)[0] if self.enabled else None

    def refused_role(self, endpoint: EndpointIR) -> str | None:
        """A declared role this endpoint must reject, if the contract has one."""
        if not self.enabled:
            return None
        allowed = set(self._allowed(endpoint))
        return next((r for r in self.roles if r not in allowed), None)

    def headers(self, endpoint: EndpointIR) -> list[str]:
        """The `headers=` argument for a call to this endpoint, if any."""
        role = self.role(endpoint)
        return [f'headers=auth_headers("{role}")'] if role else []

    def headers_for_role(self, role: str) -> list[str]:
        return [f'headers=auth_headers("{role}")']


# =============================================================================
# Source rendering helpers
# =============================================================================


def _request(assign: str, method: str, url: str, args: list[str]) -> list[str]:
    """Render one TestClient call, wrapped when it exceeds the line budget."""
    head = f"    {assign}client.{method}("
    single = head + ", ".join([url, *args]) + ")"
    if len(single) <= MAX_LINE:
        return [single]
    return [head, f"        {url},", *(f"        {a}," for a in args), "    )"]


def _url(base: str, suffix: str = "") -> str:
    """A literal URL for a fixed path."""
    return f'"{base}{suffix}"'


def _record_url(base: str, suffix: str = "") -> str:
    """An f-string URL for the record created earlier in the test body."""
    return f"f\"{base}/{{created['id']}}{suffix}\""


# =============================================================================
# Per-endpoint test emitters
# =============================================================================


@dataclass(frozen=True)
class _Ctx:
    """Everything the emitters need about one route contract."""

    entity: EntityIR
    route: RouteIR
    auth: _AuthPlan
    slug: str
    base: str
    marker: str
    missing_id: str
    endpoints: dict[tuple[str, str], EndpointIR]

    @property
    def can_create(self) -> bool:
        return ("post", "/") in self.endpoints

    def endpoint(self, method: str, path: str) -> EndpointIR | None:
        return self.endpoints.get((method, path))

    def create_call(
        self, assign: str = "created = ", payload: str = "", indent: str = "    "
    ) -> str:
        args = "client, auth_headers" if self.auth.enabled else "client"
        extra = f", {payload}" if payload else ""
        return f"{indent}{assign}_create_{self.slug}({args}{extra})"

    def signature(self, name: str) -> str:
        return f"{self.marker}def {name}({self.auth.test_args}):"


def _create_helper(ctx: _Ctx, endpoint: EndpointIR) -> list[str]:
    """The helper every test that needs an existing record calls."""
    params = "client, auth_headers, payload=None" if ctx.auth.enabled else "client, payload=None"
    status = endpoint.response_status or 201
    lines = [
        f"def _create_{ctx.slug}({params}):",
        f'    """POST one {ctx.entity.name} and return the created record."""',
        *_request(
            "resp = ",
            "post",
            _url(ctx.base, "/"),
            [
                "json=VALID_PAYLOAD if payload is None else payload",
                *ctx.auth.headers(endpoint),
            ],
        ),
        f"    assert resp.status_code == {status}, resp.text",
        "    return resp.json()",
        "",
        "",
    ]
    return lines


def _emit_create(ctx: _Ctx, endpoint: EndpointIR) -> list[str]:
    scalar = _updatable_field(ctx.entity)
    lines = [
        ctx.signature(f"test_create_{ctx.slug}"),
        f'    """POST {ctx.base}/ stores the record and returns it with an id."""',
        ctx.create_call(),
        '    assert created["id"]',
    ]
    if scalar is not None:
        lines.append(f'    assert created["{scalar.name}"] == {_default_value(scalar)}')
    lines.extend(["", ""])

    if _required_payload_fields(ctx.entity):
        lines.extend([
            ctx.signature(f"test_create_{ctx.slug}_missing_fields"),
            f'    """POST {ctx.base}/ with no body is refused by the create model."""',
            *_request(
                "resp = ", "post", _url(ctx.base, "/"), ["json={}", *ctx.auth.headers(endpoint)]
            ),
            "    assert resp.status_code == 422",
            "",
            "",
        ])

    if ctx.entity.state_machine is not None:
        initial = ctx.entity.state_machine.initial
        forbidden = _terminal_or_other_state(ctx.entity.state_machine)
        lines.extend([
            ctx.signature(f"test_create_{ctx.slug}_cannot_set_state"),
            '    """`state` is server-owned: a create that names it is refused.',
            "",
            "    Accepting it would let a caller start a record in any state at all,",
            "    which makes every transition and guard in the workflow advisory.",
            '    """',
            *_request(
                "resp = ",
                "post",
                _url(ctx.base, "/"),
                [
                    f'json={{**VALID_PAYLOAD, "state": "{forbidden}"}}',
                    *ctx.auth.headers(endpoint),
                ],
            ),
            "    assert resp.status_code == 422",
            ctx.create_call(),
            f'    assert created["state"] == "{initial}"',
            "",
            "",
        ])

    lines.extend(_emit_auth_tests(ctx, endpoint, "create", _url(ctx.base, "/"), body="json={}"))
    return lines


def _emit_list(ctx: _Ctx, endpoint: EndpointIR) -> list[str]:
    plural = pluralize(ctx.slug)
    headers = ctx.auth.headers(endpoint)
    lines: list[str] = []

    if ctx.can_create:
        lines.extend([
            ctx.signature(f"test_list_{plural}"),
            f'    """GET {ctx.base}/ returns a keyset page of the records that exist."""',
            ctx.create_call(),
            *_request("resp = ", "get", _url(ctx.base, "/"), headers),
            "    assert resp.status_code == 200",
            "    body = resp.json()",
            '    assert [item["id"] for item in body["items"]] == [created["id"]]',
            '    assert body["next_cursor"] is None',
            "",
            "",
            ctx.signature(f"test_list_{plural}_paginates"),
            '    """A full page carries a cursor; following it yields the rest, once each.',
            "",
            "    There is no `total` and no `offset`: the page is a keyset window, so",
            "    the only way to reach the next records is the cursor it hands back.",
            '    """',
            "    created_ids = set()",
            "    for _ in range(3):",
            ctx.create_call(assign="record = ", indent="        "),
            '        created_ids.add(record["id"])',
            *_request("first = ", "get", _url(ctx.base, "/"), ['params={"limit": 2}', *headers]),
            "    assert first.status_code == 200",
            '    assert len(first.json()["items"]) == 2',
            '    assert first.json()["next_cursor"] is not None',
            *_request(
                "second = ",
                "get",
                _url(ctx.base, "/"),
                ['params={"limit": 2, "cursor": first.json()["next_cursor"]}', *headers],
            ),
            "    assert second.status_code == 200",
            '    assert len(second.json()["items"]) == 1',
            '    assert second.json()["next_cursor"] is None',
            '    seen = {item["id"] for item in first.json()["items"] + second.json()["items"]}',
            "    assert seen == created_ids",
            "",
            "",
        ])

    lines.extend([
        ctx.signature(f"test_list_{plural}_bounds_limit"),
        '    """`limit` is bounded, so one request cannot ask for the whole table."""',
        *_request("huge = ", "get", _url(ctx.base, "/"), ['params={"limit": 999999999}', *headers]),
        "    assert huge.status_code == 422",
        *_request("zero = ", "get", _url(ctx.base, "/"), ['params={"limit": 0}', *headers]),
        "    assert zero.status_code == 422",
        "",
        "",
        ctx.signature(f"test_list_{plural}_rejects_forged_cursor"),
        '    """A truncated or hand-edited cursor is client error, not a 500."""',
        *_request(
            "resp = ", "get", _url(ctx.base, "/"), ['params={"cursor": "not-a-cursor"}', *headers]
        ),
        "    assert resp.status_code == 400",
        "",
        "",
    ])

    lines.extend(_emit_auth_tests(ctx, endpoint, "list", _url(ctx.base, "/")))
    return lines


def _emit_get(ctx: _Ctx, endpoint: EndpointIR) -> list[str]:
    headers = ctx.auth.headers(endpoint)
    lines: list[str] = []

    if ctx.can_create:
        lines.extend([
            ctx.signature(f"test_get_{ctx.slug}"),
            f'    """GET {ctx.base}/{{id}} returns the record that was created."""',
            ctx.create_call(),
            *_request("resp = ", "get", _record_url(ctx.base), headers),
            "    assert resp.status_code == 200",
            '    assert resp.json()["id"] == created["id"]',
            "",
            "",
        ])

    lines.extend([
        ctx.signature(f"test_get_{ctx.slug}_not_found"),
        f'    """GET {ctx.base}/{{id}} for an absent record is a 404."""',
        *_request("resp = ", "get", _url(ctx.base, f"/{ctx.missing_id}"), headers),
        "    assert resp.status_code == 404",
        "",
        "",
    ])

    if _id_is_uuid(ctx.entity):
        lines.extend([
            ctx.signature(f"test_get_{ctx.slug}_malformed_id"),
            '    """An id of the wrong shape is rejected at the boundary, not by the driver."""',
            *_request("resp = ", "get", _url(ctx.base, "/not-a-uuid"), headers),
            "    assert resp.status_code == 422",
            "",
            "",
        ])

    lines.extend(
        _emit_auth_tests(ctx, endpoint, "get", _url(ctx.base, f"/{ctx.missing_id}"))
    )
    return lines


def _emit_update(ctx: _Ctx, endpoint: EndpointIR) -> list[str]:
    headers = ctx.auth.headers(endpoint)
    field = _updatable_field(ctx.entity)
    patch = (
        f'json={{"{field.name}": {_alternate_value(field)}}}'
        if field is not None
        else "json={}"
    )
    lines: list[str] = []

    if ctx.can_create and field is not None:
        lines.extend([
            ctx.signature(f"test_update_{ctx.slug}"),
            f'    """PATCH {ctx.base}/{{id}} applies the change and returns the new value."""',
            ctx.create_call(),
            *_request("resp = ", "patch", _record_url(ctx.base), [patch, *headers]),
            "    assert resp.status_code == 200",
            f'    assert resp.json()["{field.name}"] == {_alternate_value(field)}',
            "",
            "",
        ])

    lines.extend([
        ctx.signature(f"test_update_{ctx.slug}_not_found"),
        f'    """PATCH {ctx.base}/{{id}} for an absent record is a 404."""',
        *_request(
            "resp = ", "patch", _url(ctx.base, f"/{ctx.missing_id}"), [patch, *headers]
        ),
        "    assert resp.status_code == 404",
        "",
        "",
    ])

    if ctx.can_create and ctx.entity.state_machine is not None:
        sm = ctx.entity.state_machine
        target = _terminal_or_other_state(sm)
        lines.extend([
            ctx.signature(f"test_update_{ctx.slug}_cannot_set_state"),
            '    """`state` is not writable through the update endpoint.',
            "",
            f"    The only writer is PUT {ctx.base}/{{id}}/state, which is the only path",
            "    that consults the machine's transitions and guards.",
            '    """',
            ctx.create_call(),
            *_request(
                "resp = ",
                "patch",
                _record_url(ctx.base),
                [f'json={{"state": "{target}"}}', *headers],
            ),
            "    assert resp.status_code == 422",
            *_request("after = ", "get", _record_url(ctx.base), _detail_headers(ctx, headers)),
            f'    assert after.json()["state"] == "{sm.initial}"'
            if ctx.endpoint("get", "/{id}")
            else f'    assert created["state"] == "{sm.initial}"',
            "",
            "",
        ])

    lines.extend(
        _emit_auth_tests(
            ctx, endpoint, "update", _url(ctx.base, f"/{ctx.missing_id}"), body="json={}"
        )
    )
    return lines


def _detail_headers(ctx: _Ctx, fallback: list[str]) -> list[str]:
    """Headers for the detail endpoint, which may permit a different role."""
    detail = ctx.endpoint("get", "/{id}")
    return ctx.auth.headers(detail) if detail is not None else fallback


def _emit_delete(ctx: _Ctx, endpoint: EndpointIR) -> list[str]:
    headers = ctx.auth.headers(endpoint)
    status = endpoint.response_status or 204
    lines: list[str] = []

    if ctx.can_create:
        body = [
            ctx.signature(f"test_delete_{ctx.slug}"),
            f'    """DELETE {ctx.base}/{{id}} removes the record."""',
            ctx.create_call(),
            *_request("resp = ", "delete", _record_url(ctx.base), headers),
            f"    assert resp.status_code == {status}",
        ]
        detail = ctx.endpoint("get", "/{id}")
        if detail is not None:
            body.extend([
                *_request("gone = ", "get", _record_url(ctx.base), ctx.auth.headers(detail)),
                "    assert gone.status_code == 404",
            ])
        lines.extend([*body, "", ""])

    lines.extend([
        ctx.signature(f"test_delete_{ctx.slug}_not_found"),
        f'    """DELETE {ctx.base}/{{id}} for an absent record is a 404."""',
        *_request("resp = ", "delete", _url(ctx.base, f"/{ctx.missing_id}"), headers),
        "    assert resp.status_code == 404",
        "",
        "",
    ])

    lines.extend(
        _emit_auth_tests(ctx, endpoint, "delete", _url(ctx.base, f"/{ctx.missing_id}"))
    )
    return lines


# =============================================================================
# Transition tests
# =============================================================================


def _terminal_or_other_state(sm: StateMachineIR) -> str:
    """A declared state that is not the initial one, for bypass-attempt payloads."""
    return next((s.name for s in sm.states if s.name != sm.initial), sm.initial)


def _unreachable_state(sm: StateMachineIR) -> str | None:
    """A declared state the machine forbids moving to from `initial`."""
    reachable = set(sm.transitions.get(sm.initial, []))
    return next(
        (s.name for s in sm.states if s.name != sm.initial and s.name not in reachable), None
    )


def _guard_for(sm: StateMachineIR, source: str, target: str) -> GuardIR | None:
    return next(
        (
            g
            for g in sm.guards
            if g.from_state == source and g.to_state == target and g.require_fields
        ),
        None,
    )


def _transition_path(sm: StateMachineIR, start: str, end: str) -> list[str] | None:
    """BFS for the states to move through to get from `start` to `end`."""
    if start == end:
        return []
    visited = {start}
    queue: list[tuple[str, list[str]]] = [(start, [])]
    while queue:
        current, path = queue.pop(0)
        for target in sm.transitions.get(current, []):
            if target == end:
                return path + [target]
            if target not in visited:
                visited.add(target)
                queue.append((target, path + [target]))
    return None


def _provokable_guard(ctx: _Ctx, sm: StateMachineIR, guard: GuardIR) -> list[str] | None:
    """The states to walk to provoke `guard`, or None if it cannot be provoked.

    A guard can only be made to fail when every field it requires can be left
    unset by a create, and when the record can be driven to the guard's source
    state without crossing another guard that needs one of those same fields.
    """
    omittable = {
        f.name for f in _payload_fields(ctx.entity) if not f.required and f.default is None
    }
    if not set(guard.require_fields) <= omittable:
        return None

    path = _transition_path(sm, sm.initial, guard.from_state)
    if path is None:
        return None

    source = sm.initial
    for target in path:
        crossed = _guard_for(sm, source, target)
        if crossed is not None and set(crossed.require_fields) & set(guard.require_fields):
            return None
        source = target
    return path


def _emit_transition(ctx: _Ctx, endpoint: EndpointIR) -> list[str]:
    sm = ctx.entity.state_machine
    if sm is None:
        raise GenerationError(
            f"Route {ctx.route.fqn!r} declares PUT {endpoint.path!r}, but entity "
            f"{ctx.entity.fqn!r} binds no workflow, so there is no transition to test."
        )
    if not ctx.can_create:
        return _emit_auth_tests(
            ctx, endpoint, "transition", _url(ctx.base, f"/{ctx.missing_id}/state"), body="json={}"
        )

    headers = ctx.auth.headers(endpoint)
    lines: list[str] = []
    targets = sm.transitions.get(sm.initial, [])

    if targets:
        target = targets[0]
        guard = _guard_for(sm, sm.initial, target)
        satisfied = ""
        if guard is not None:
            settable = {c.name for c in _payload_fields(ctx.entity)}
            unsatisfiable = [
                f
                for f in guard.require_fields
                if f not in settable and not _auto_populated(ctx.entity, f)
            ]
            if unsatisfiable:
                raise GenerationError(
                    f"{ctx.entity.fqn}: the guard on {sm.initial!r} -> {target!r} "
                    f"requires {unsatisfiable}, which no client can set — the "
                    f"field is neither writable nor server-populated. The "
                    f"transition can never be performed through the API."
                )
            satisfied = (
                f"    # VALID_PAYLOAD sets {', '.join(guard.require_fields)}, which this "
                f"edge's guard requires."
            )
        lines.extend([
            ctx.signature(f"test_transition_{ctx.slug}"),
            f'    """PUT {ctx.base}/{{id}}/state moves {sm.initial} -> {target}."""',
            ctx.create_call(),
            *([satisfied] if satisfied else []),
            *_request(
                "resp = ",
                "put",
                _record_url(ctx.base, "/state"),
                [f'json={{"state": "{target}"}}', *headers],
            ),
            "    assert resp.status_code == 200",
            f'    assert resp.json()["state"] == "{target}"',
            "",
            "",
        ])

    unreachable = _unreachable_state(sm)
    if unreachable is not None:
        lines.extend([
            ctx.signature(f"test_transition_{ctx.slug}_invalid"),
            '    """A move the machine has no edge for is a 409, not a 404 and not a 422.',
            "",
            "    The three refusal causes are distinguishable on purpose: the caller",
            "    has to tell a missing record from an illegal move from a failed guard.",
            '    """',
            ctx.create_call(),
            *_request(
                "resp = ",
                "put",
                _record_url(ctx.base, "/state"),
                [f'json={{"state": "{unreachable}"}}', *headers],
            ),
            "    assert resp.status_code == 409",
            '    assert resp.json()["detail"]["error"] == "invalid_transition"',
            "",
            "",
        ])

    lines.extend([
        ctx.signature(f"test_transition_{ctx.slug}_unknown_state"),
        '    """A state the machine does not declare is refused, not stored."""',
        ctx.create_call(),
        *_request(
            "resp = ",
            "put",
            _record_url(ctx.base, "/state"),
            [f'json={{"state": "{UNKNOWN_STATE}"}}', *headers],
        ),
        "    assert resp.status_code == 409",
        '    assert resp.json()["detail"]["error"] == "invalid_transition"',
        "",
        "",
        ctx.signature(f"test_transition_{ctx.slug}_not_found"),
        '    """Transitioning an absent record is a 404, distinct from an illegal move."""',
        *_request(
            "resp = ",
            "put",
            _url(ctx.base, f"/{ctx.missing_id}/state"),
            ['json={"state": "' + (targets[0] if targets else UNKNOWN_STATE) + '"}', *headers],
        ),
        "    assert resp.status_code == 404",
        '    assert resp.json()["detail"]["error"] == "not_found"',
        "",
        "",
        ctx.signature(f"test_transition_{ctx.slug}_missing_state"),
        '    """A body with no target state is rejected by the request model."""',
        ctx.create_call(),
        *_request("resp = ", "put", _record_url(ctx.base, "/state"), ["json={}", *headers]),
        "    assert resp.status_code == 422",
        "",
        "",
    ])

    for guard in sm.guards:
        if guard.require_fields:
            lines.extend(_emit_guard_test(ctx, sm, guard, endpoint))

    lines.extend(
        _emit_auth_tests(
            ctx, endpoint, "transition", _url(ctx.base, f"/{ctx.missing_id}/state"), body="json={}"
        )
    )
    return lines


def _auto_populated(entity: EntityIR, field_name: str) -> bool:
    """Whether the server fills this field in without the client asking."""
    field = next((f for f in entity.fields if f.name == field_name), None)
    return field is not None and bool(field.computed)


def _emit_guard_test(
    ctx: _Ctx, sm: StateMachineIR, guard: GuardIR, endpoint: EndpointIR
) -> list[str]:
    path = _provokable_guard(ctx, sm, guard)
    if path is None:
        return []

    headers = ctx.auth.headers(endpoint)
    omitted = tuple(guard.require_fields)
    omitted_literal = ", ".join(f'"{name}"' for name in omitted)
    name = py_identifier(f"test_transition_{ctx.slug}_{guard.from_state}_to_{guard.to_state}_guard")

    lines = [
        ctx.signature(name),
        f'    """{guard.from_state} -> {guard.to_state} is refused until '
        f'{", ".join(omitted)} is set."""',
        f"    omitted = ({omitted_literal},)",
        "    payload = {k: v for k, v in VALID_PAYLOAD.items() if k not in omitted}",
        ctx.create_call(payload="payload"),
    ]
    for step in path:
        lines.extend([
            *_request(
                "step = ",
                "put",
                _record_url(ctx.base, "/state"),
                [f'json={{"state": "{step}"}}', *headers],
            ),
            "    assert step.status_code == 200, step.text",
        ])
    lines.extend([
        *_request(
            "resp = ",
            "put",
            _record_url(ctx.base, "/state"),
            [f'json={{"state": "{guard.to_state}"}}', *headers],
        ),
        "    assert resp.status_code == 422",
        '    assert resp.json()["detail"]["error"] == "guard_failed"',
    ])

    update = ctx.endpoint("patch", "/{id}")
    if update is not None:
        fields = ", ".join(
            f'"{n}": {_default_value(_field(ctx.entity, n))}' for n in omitted
        )
        lines.extend([
            "",
            *_request(
                "filled = ",
                "patch",
                _record_url(ctx.base),
                [f"json={{{fields}}}", *ctx.auth.headers(update)],
            ),
            "    assert filled.status_code == 200, filled.text",
            *_request(
                "allowed = ",
                "put",
                _record_url(ctx.base, "/state"),
                [f'json={{"state": "{guard.to_state}"}}', *headers],
            ),
            "    assert allowed.status_code == 200, allowed.text",
            f'    assert allowed.json()["state"] == "{guard.to_state}"',
        ])

    lines.extend(["", ""])
    return lines


def _field(entity: EntityIR, name: str) -> FieldIR:
    field = next((f for f in entity.fields if f.name == name), None)
    if field is None:
        raise GenerationError(
            f"{entity.fqn}: a workflow guard names field {name!r}, which the entity "
            f"does not declare. The guard can never be satisfied."
        )
    return field


# =============================================================================
# Write-only field tests
# =============================================================================


def _emit_sensitive_tests(ctx: _Ctx) -> list[str]:
    """Assert a `sensitive: true` value never reaches a client."""
    sensitive = _sensitive_fields(ctx.entity)
    create = ctx.endpoint("post", "/")
    if not sensitive or create is None:
        return []

    names = ", ".join(f'"{f.name}"' for f in sensitive)
    # The sentinel only works for a field whose value is free text; for anything
    # else the key-absence assertions carry the test on their own.
    textual = next((f for f in sensitive if f.type in ("string", "text")), None)
    payload = (
        f'{{**VALID_PAYLOAD, "{textual.name}": SENSITIVE_SENTINEL}}'
        if textual is not None
        else "VALID_PAYLOAD"
    )

    lines = [
        ctx.signature(f"test_{ctx.slug}_write_only_fields_never_disclosed"),
        f'    """{", ".join(f.name for f in sensitive)} is accepted on write and',
        "    absent from every response.",
        "",
        "    Checked on the raw body rather than the parsed one: the point is that the",
        "    value never leaves the process, by any serialisation path.",
        '    """',
        *_request(
            "created = ",
            "post",
            _url(ctx.base, "/"),
            [f"json={payload}", *ctx.auth.headers(create)],
        ),
        "    assert created.status_code == 201, created.text",
        f"    for name in ({names},):",
        "        assert name not in created.json()",
    ]
    if textual is not None:
        lines.append("    assert SENSITIVE_SENTINEL not in created.text")

    detail = ctx.endpoint("get", "/{id}")
    if detail is not None:
        lines.extend([
            '    record_id = created.json()["id"]',
            *_request(
                "fetched = ",
                "get",
                f'f"{ctx.base}/{{record_id}}"',
                ctx.auth.headers(detail),
            ),
            "    assert fetched.status_code == 200",
            f"    for name in ({names},):",
            "        assert name not in fetched.json()",
        ])
        if textual is not None:
            lines.append("    assert SENSITIVE_SENTINEL not in fetched.text")

    listing = ctx.endpoint("get", "/")
    if listing is not None:
        lines.extend([
            *_request("page = ", "get", _url(ctx.base, "/"), ctx.auth.headers(listing)),
            "    assert page.status_code == 200",
            f"    for name in ({names},):",
            '        assert all(name not in item for item in page.json()["items"])',
        ])
        if textual is not None:
            lines.append("    assert SENSITIVE_SENTINEL not in page.text")

    update = ctx.endpoint("patch", "/{id}")
    if update is not None and textual is not None:
        lines.extend([
            '    record_id = created.json()["id"]',
            *_request(
                "patched = ",
                "patch",
                f'f"{ctx.base}/{{record_id}}"',
                [
                    f'json={{"{textual.name}": SENSITIVE_SENTINEL + "-rotated"}}',
                    *ctx.auth.headers(update),
                ],
            ),
            "    assert patched.status_code == 200, patched.text",
            "    assert SENSITIVE_SENTINEL not in patched.text",
        ])

    lines.extend(["", ""])
    return lines


# =============================================================================
# Authentication / authorization tests
# =============================================================================


def _emit_auth_tests(
    ctx: _Ctx,
    endpoint: EndpointIR,
    action: str,
    path_expr: str,
    body: str | None = None,
) -> list[str]:
    """401 and 403 tests for one endpoint."""
    if not ctx.auth.enabled:
        return []

    method = endpoint.method.lower()
    args = [body] if body else []
    lines = [
        f"{ctx.marker}def test_{action}_{ctx.slug}_unauthenticated(client):",
        f'    """{endpoint.method.upper()} without a token is a 401."""',
        *_request("resp = ", method, path_expr, args),
        "    assert resp.status_code == 401",
        "",
        "",
    ]

    refused = ctx.auth.refused_role(endpoint)
    if refused:
        lines.extend([
            ctx.signature(f"test_{action}_{ctx.slug}_wrong_role"),
            f'    """{endpoint.method.upper()} as {refused!r} is a 403: the contract '
            f'does not permit it."""',
            *_request("resp = ", method, path_expr, [*args, *ctx.auth.headers_for_role(refused)]),
            "    assert resp.status_code == 403",
            "",
            "",
        ])

    return lines


# =============================================================================
# Module assembly
# =============================================================================


_EMITTERS = {
    ("post", "/"): _emit_create,
    ("get", "/"): _emit_list,
    ("get", "/{id}"): _emit_get,
    ("patch", "/{id}"): _emit_update,
    ("delete", "/{id}"): _emit_delete,
    ("put", "/{id}/state"): _emit_transition,
}


def _canonical(path: str) -> str:
    p = path.strip()
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") or "/"


def _generate_entity_tests(
    ir: DomainIR, route: RouteIR, entity: EntityIR, auth_infra: InfraIR | None
) -> GeneratedFile:
    slug = module_slug(entity.name, entity.domain, multi_domain=ir.multi_domain)
    base = (route.base_path or f"/{pluralize(slug)}").rstrip("/")
    endpoints = {(e.method.lower(), _canonical(e.path)): e for e in route.endpoints}
    ctx = _Ctx(
        entity=entity,
        route=route,
        auth=_AuthPlan(route, base, auth_infra),
        slug=slug,
        base=base,
        marker="@pytest.mark.requires_pipeline\n" if entity.ai_hooks else "",
        missing_id=_missing_id(entity),
        endpoints=endpoints,
    )

    lines = [
        provenance_header("python", route.fqn, f"Behavioural tests for the {entity.name} API"),
        "from __future__ import annotations",
        "",
    ]
    if ctx.marker:
        lines.extend(["import pytest", ""])
    lines.extend(["", f"VALID_PAYLOAD = {_valid_payload_code(entity)}"])
    if _sensitive_fields(entity):
        lines.append(f'SENSITIVE_SENTINEL = "{SENSITIVE_SENTINEL}"')
    lines.extend(["", ""])

    create = ctx.endpoint("post", "/")
    if create is not None:
        lines.extend(_create_helper(ctx, create))

    for key, endpoint in endpoints.items():
        emitter = _EMITTERS.get(key)
        if emitter is None:
            raise GenerationError(
                f"Route {route.fqn!r} declares {endpoint.method.upper()} "
                f"{endpoint.path!r}, which the test generator cannot exercise. "
                f"Emitting no test for an endpoint that ships is how an endpoint "
                f"comes to have no coverage at all."
            )
        lines.extend(emitter(ctx, endpoint))

    lines.extend(_emit_sensitive_tests(ctx))

    return GeneratedFile(
        path=f"backend/tests/test_{slug}.py",
        content="\n".join(lines).rstrip("\n") + "\n",
        provenance=route.fqn,
    )


# =============================================================================
# conftest.py
# =============================================================================


def _generate_conftest(ir: DomainIR) -> GeneratedFile:
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    header = provenance_header(
        "python", f"domain/{ir.domain}", "Test configuration and fixtures"
    )

    lines = [
        header,
        "# Running this suite:",
        "#",
        "#     pip install -r requirements.txt -r requirements-dev.txt",
        "#     python -m pytest backend/tests",
        "#",
        "# pytest is in requirements-dev.txt, which the runtime Dockerfile",
        "# deliberately does not copy: the image that serves traffic carries no test",
        "# tooling.",
        "from __future__ import annotations",
        "",
        "import os",
        "",
        "# Set before backend.config is imported. It reads the environment once, at",
        "# import time, and refuses to boot on a missing or too-short AUTH_SECRET.",
        'os.environ["DATABASE_BACKEND"] = "memory"',
    ]
    if auth_infra:
        lines.extend([
            f'os.environ.setdefault("AUTH_SECRET", "{TEST_AUTH_SECRET}")',
            'os.environ.setdefault("AUTH_ENABLED", "true")',
        ])
    lines.extend([
        "",
        "import pytest  # noqa: E402",
        "from starlette.testclient import TestClient  # noqa: E402",
        "",
        "from backend.app import app  # noqa: E402",
        "from backend.repositories.memory import reset_stores  # noqa: E402",
    ])
    if auth_infra:
        lines.extend([
            "",
            "import asyncio  # noqa: E402",
            "",
            "from backend.auth.interface import AuthUser  # noqa: E402",
            "from backend.auth.jwt_provider import JWTAuthProvider  # noqa: E402",
        ])

    lines.extend([
        "",
        "",
        "def pytest_configure(config):",
        "    config.addinivalue_line(",
        '        "markers",',
        '        "requires_pipeline: requires ML pipeline (skipped unless SPECORA_TEST_FULL=1)",',
        "    )",
        "",
        "",
        "def pytest_collection_modifyitems(config, items):",
        '    if os.environ.get("SPECORA_TEST_FULL"):',
        "        return",
        '    skip = pytest.mark.skip(reason="requires ML pipeline (set SPECORA_TEST_FULL=1)")',
        "    for item in items:",
        '        if "requires_pipeline" in item.keywords:',
        "            item.add_marker(skip)",
        "",
        "",
        "@pytest.fixture(autouse=True)",
        "def _empty_stores():",
        '    """Start and finish every test with an empty database.',
        "",
        "    The in-memory adapter's stores are process-global, as they must be for",
        "    every request in a process to see the same rows. Without this fixture a",
        "    row created by one test is visible to the next, and a count assertion",
        "    passes on its own and fails in the suite.",
        '    """',
        "    reset_stores()",
        "    yield",
        "    reset_stores()",
        "",
        "",
        "@pytest.fixture",
        "def client():",
        '    """TestClient backed by the in-memory repository."""',
        "    with TestClient(app) as test_client:",
        "        yield test_client",
        "",
    ])

    if auth_infra:
        roles = [str(r) for r in (auth_infra.config.get("roles") or [])] or ["admin"]
        lines.extend([
            "",
            "def make_auth_headers(role: str) -> dict:",
            '    """Bearer headers for `role`, minted by the application\'s own issuer.',
            "",
            "    Deliberately not hand-assembled. The verifier requires typ/iat/iss/aud",
            "    and checks the issuer and audience, so a fixture that builds its own",
            "    claim set drifts out of step the moment the provider changes — and then",
            "    every test in the suite fails with 401 instead of testing anything.",
            '    """',
            "    pair = asyncio.run(",
            "        JWTAuthProvider().issue_tokens(",
            "            AuthUser(",
            '                id="00000000-0000-0000-0000-0000000000aa",',
            '                email="tests@specora.invalid",',
            "                role=role,",
            "            )",
            "        )",
            "    )",
            '    return {"Authorization": f"Bearer {pair.access_token}"}',
            "",
            "",
            "@pytest.fixture",
            "def auth_headers():",
            '    """Factory: `auth_headers("admin")` -> headers carrying that role."""',
            "    return make_auth_headers",
            "",
            "",
            "@pytest.fixture",
            "def admin_headers():",
            f'    """Auth headers for the {roles[0]!r} role."""',
            f'    return make_auth_headers("{roles[0]}")',
            "",
        ])

    return GeneratedFile(
        path="backend/tests/conftest.py",
        content="\n".join(lines).rstrip("\n") + "\n",
        provenance=f"domain/{ir.domain}",
    )


# =============================================================================
# Orchestrator
# =============================================================================


def generate_tests(ir: DomainIR) -> list[GeneratedFile]:
    """Generate the pytest suite for a domain's API routes."""
    if not ir.routes:
        return []

    entity_map = {e.fqn: e for e in ir.entities}
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    files: list[GeneratedFile] = [_generate_conftest(ir)]

    for route in ir.routes:
        entity = entity_map.get(route.entity_fqn)
        if entity is None:
            raise GenerationError(
                f"Route {route.fqn!r} manages entity {route.entity_fqn!r}, which is "
                f"not in the compiled IR. Its tests would call an API that does not exist."
            )
        files.append(_generate_entity_tests(ir, route, entity, auth_infra))

    return files

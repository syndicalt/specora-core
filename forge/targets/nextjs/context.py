"""Resolved, collision-free names for every generated frontend artifact.

The four sub-generators (api client, pages, components, layout) all need the
same three answers about an entity: what its React component is called, which
api-client export lists it, and what URL its page lives at. Each used to derive
those independently — three private copies of `_to_pascal`, plus `name + "s"`
for the api export — so they could disagree, and none of them was domain-aware.

Both consequences were real:

  * `entity/billing/account` and `entity/support/account` in one build produced
    the same `AccountTable.tsx` and the same `/accounts` route. The second
    silently overwrote the first.

  * The api export was guessed as `entity_name + "s"` while the api client
    actually exports the *route contract's* name. A route contract named
    anything else produced components importing a binding that does not exist,
    which fails at `next build`, not at generation.

Everything name-shaped is resolved once here, from `forge.targets.naming`, and
read back through `FrontendContext`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from forge.ir.model import DomainIR, EndpointIR, EntityIR, InfraIR, PageIR
from forge.targets.base import GenerationError
from forge.targets.naming import camel_case, class_name, module_slug, py_identifier

#: `{id}`-style path parameters in a contract's endpoint path.
PATH_PARAM = re.compile(r"\{([^}]+)\}")

#: Where an unauthenticated caller is sent. Fixed rather than derived so the
#: session module, the gate and the login page cannot disagree about it.
LOGIN_ROUTE = "/login"

#: Default credential field, matching gen_app's `_DEFAULT_IDENTITY_FIELD`. The
#: login form has to post the field name the handler declares in its request
#: model, or every sign-in is a 422.
_DEFAULT_IDENTITY_FIELD = "email"


@dataclass(frozen=True)
class AuthSpec:
    """What the frontend needs to know about the domain's auth contract.

    Attributes:
        fqn: The infra contract this was resolved from, for provenance.
        identity_field: The field name `POST /auth/login` expects alongside
            `password` (`config.identity_field`, defaulting to `email`).
        identity_input_type: The HTML input type for that field.
        identity_label: Human-readable label for the login form.
    """

    fqn: str
    identity_field: str
    identity_input_type: str
    identity_label: str


def endpoint_method_name(endpoint: EndpointIR) -> str:
    """The api-client method an endpoint compiles to.

    Shared by the client generator (which emits the method) and by the page
    generator (which must not render a Delete button for a route contract that
    declares no DELETE endpoint — the binding would not exist and the page
    would fail to compile).
    """
    method = endpoint.method.upper()
    path = endpoint.path or "/"
    params = PATH_PARAM.findall(path)

    if method == "GET" and not params:
        return "list"
    if method == "GET" and params == ["id"]:
        return "get"
    if method == "POST" and not params:
        return "create"
    if method in ("PATCH", "PUT") and params == ["id"] and not path.rstrip("/").endswith("state"):
        return "update"
    if method == "DELETE" and params == ["id"]:
        return "remove"
    if method in ("PUT", "POST") and params == ["id"] and path.rstrip("/").endswith("state"):
        return "transition"

    static = [seg for seg in path.strip("/").split("/") if seg and not seg.startswith("{")]
    stem = "_".join(static)
    return camel_case(py_identifier(stem if stem else f"{method.lower()}_root"))


@dataclass(frozen=True)
class EntityView:
    """One entity's page, with every generated name it needs already resolved.

    Attributes:
        entity: The entity being displayed.
        page: The page contract that displays it.
        component: React component stem — `Ticket` gives `TicketTable`,
            `TicketForm`, `TicketDetail`, `TicketKanban`.
        api: The api-client export that talks to this entity's endpoints.
        url: The browser path of the list view, always leading-slashed.
        binding: A camelCase local binding for use inside generated TSX.
        methods: The api-client methods this entity's route actually exposes.
    """

    entity: EntityIR
    page: PageIR
    component: str
    api: str
    url: str
    binding: str
    methods: frozenset[str]

    @property
    def app_dir(self) -> str:
        """The App Router directory for this page, relative to `src/app`."""
        return self.url.strip("/")


class FrontendContext:
    """Every name the Next.js generators share, resolved once from the IR.

    Attributes:
        ir: The compiled domain.
        views: One `EntityView` per page contract, in contract order.
        auth: The domain's auth spec, or None when it declares no auth.
    """

    def __init__(self, ir: DomainIR) -> None:
        self.ir = ir
        self.auth = _resolve_auth(ir)

        entity_by_fqn = {e.fqn: e for e in ir.entities}

        # The api client exports one binding per *route* contract, so a
        # component that wants to call an entity's endpoints has to look the
        # binding up by the entity the route manages — it is not derivable
        # from the entity name.
        self._api_by_entity: dict[str, str] = {
            route.entity_fqn: module_slug(route.name, route.domain, multi_domain=ir.multi_domain)
            for route in ir.routes
            if route.entity_fqn
        }

        self._methods_by_entity: dict[str, frozenset[str]] = {
            route.entity_fqn: frozenset(endpoint_method_name(e) for e in route.endpoints)
            for route in ir.routes
            if route.entity_fqn
        }

        self.views: list[EntityView] = []
        self._view_by_entity: dict[str, EntityView] = {}
        for page in ir.pages:
            entity = entity_by_fqn.get(page.entity_fqn)
            if entity is None:
                continue
            view = EntityView(
                entity=entity,
                page=page,
                component=class_name(entity.name, entity.domain, multi_domain=ir.multi_domain),
                api=self._api_by_entity.get(page.entity_fqn, ""),
                url=_page_url(page, multi_domain=ir.multi_domain),
                binding=camel_case(
                    class_name(entity.name, entity.domain, multi_domain=ir.multi_domain)
                ),
                methods=self._methods_by_entity.get(page.entity_fqn, frozenset()),
            )
            self.views.append(view)
            # Two pages may bind the same entity. Components are per-entity, so
            # the first page wins for component generation and the rest reuse
            # it; generating both would claim the same output path twice.
            self._view_by_entity.setdefault(entity.fqn, view)

        if self.auth is not None:
            self._reject_login_route_collision()

    @property
    def component_views(self) -> list[EntityView]:
        """One view per entity — the set that entity components are built from."""
        return list(self._view_by_entity.values())

    def api_for_entity(self, entity_fqn: str) -> str:
        """The api-client export for an entity, or "" when it has no route.

        An entity with no route contract has no endpoints, so a reference to it
        cannot be resolved to a display name. Callers fall back to rendering
        the raw identifier rather than importing a binding that is not there.
        """
        return self._api_by_entity.get(entity_fqn, "")

    def view_for_entity(self, entity_fqn: str) -> EntityView | None:
        """The page view for an entity, or None when no page displays it."""
        return self._view_by_entity.get(entity_fqn)

    def component_for_entity(self, entity_fqn: str) -> str:
        """The React/TypeScript stem for an entity, resolved across domains.

        Used for reference targets, which may live in a different domain than
        the entity referencing them — so the name depends on the *target's*
        domain, not the caller's.
        """
        for entity in self.ir.entities:
            if entity.fqn == entity_fqn:
                return class_name(entity.name, entity.domain, multi_domain=self.ir.multi_domain)
        return ""

    def entity_has_field(self, entity_fqn: str, field_name: str) -> bool:
        """Whether an entity declares a field, used to validate references."""
        for entity in self.ir.entities:
            if entity.fqn == entity_fqn:
                return any(f.name == field_name for f in entity.fields)
        return False

    def _reject_login_route_collision(self) -> None:
        """Fail if a page contract claims the path the login page needs."""
        for view in self.views:
            if view.url.rstrip("/") == LOGIN_ROUTE:
                raise GenerationError(
                    f"{view.page.fqn} routes to {LOGIN_ROUTE!r}, which is also "
                    f"where the generated sign-in page lives because "
                    f"{self.auth.fqn} declares authentication. One would "
                    f"overwrite the other. Give the page a different "
                    f"spec.route."
                )


def _page_url(page: PageIR, *, multi_domain: bool) -> str:
    """The browser path for a page, namespaced by domain in a multi-domain build.

    Page routes come from contracts, and two domains are each free to publish a
    `/accounts` page. Prefixing with the domain keeps the App Router
    directories distinct; single-domain output keeps the contract's route
    verbatim.
    """
    stem = (page.route or f"/{page.name}").strip("/")
    if not stem:
        raise GenerationError(
            f"{page.fqn}: spec.route is empty, so the page has no URL. It would "
            f"collide with the dashboard at '/'."
        )
    return f"/{page.domain}/{stem}" if multi_domain else f"/{stem}"


def _resolve_auth(ir: DomainIR) -> AuthSpec | None:
    """Resolve the login form's shape from the domain's auth infra contract.

    Only the credential field is read here. Whether a *backend* login handler
    exists depends on `config.user_entity`, which is the API generator's
    concern; the frontend always renders sign-in when auth is declared, because
    every protected endpoint 401s without it.
    """
    infra: InfraIR | None = next((i for i in ir.infra if i.category == "auth"), None)
    if infra is None:
        return None

    field_name = str(infra.config.get("identity_field", _DEFAULT_IDENTITY_FIELD))
    field_type = _identity_field_type(ir, infra, field_name)

    return AuthSpec(
        fqn=infra.fqn,
        identity_field=field_name,
        # `type="email"` gets the browser's own validation and the right mobile
        # keyboard; anything else stays `text` so a username is not rejected
        # client-side for not looking like an address.
        identity_input_type="email" if field_type == "email" else "text",
        identity_label=field_name.replace("_", " ").title(),
    )


def _identity_field_type(ir: DomainIR, infra: InfraIR, field_name: str) -> str:
    """The IR type of the credential field on the contract's user entity."""
    user_entity_fqn = infra.config.get("user_entity")
    if not user_entity_fqn:
        # Without a user entity the API generator emits no login handler; the
        # default field is `email`, so assume that shape.
        return "email" if field_name == _DEFAULT_IDENTITY_FIELD else "string"

    entity = next((e for e in ir.entities if e.fqn == user_entity_fqn), None)
    if entity is None:
        # The API generator raises on this; do not raise a second, differently
        # worded error for the same contract defect.
        return "string"

    field = next((f for f in entity.fields if f.name == field_name), None)
    return field.type if field else "string"

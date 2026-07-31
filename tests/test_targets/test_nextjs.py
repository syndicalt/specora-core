"""Tests for the Next.js frontend generator."""

import json

import pytest

from forge.ir.model import DomainIR, EndpointIR, EntityIR, FieldIR, PageIR, RouteIR


@pytest.fixture
def helpdesk_ir() -> DomainIR:
    """A minimal helpdesk IR for testing frontend generation."""
    return DomainIR(
        domain="helpdesk",
        entities=[
            EntityIR(
                fqn="entity/helpdesk/ticket",
                name="ticket",
                domain="helpdesk",
                table_name="tickets",
                icon="ticket",
                fields=[
                    FieldIR(name="subject", type="string", required=True),
                    FieldIR(
                        name="priority",
                        type="string",
                        enum_values=["critical", "high", "medium", "low"],
                    ),
                    FieldIR(name="id", type="uuid", computed="uuid"),
                    FieldIR(name="created_at", type="datetime", computed="now"),
                ],
            ),
        ],
        pages=[
            PageIR(
                fqn="page/helpdesk/tickets",
                name="tickets",
                domain="helpdesk",
                route="/tickets",
                title="Support Tickets",
                entity_fqn="entity/helpdesk/ticket",
                data_sources=[{"endpoint": "/tickets", "alias": "tickets"}],
                views=[
                    {"type": "table", "default": True, "columns": ["subject", "priority"]},
                    {"type": "kanban", "card_fields": ["subject", "priority"]},
                ],
            ),
        ],
        routes=[
            RouteIR(
                fqn="route/helpdesk/tickets",
                name="tickets",
                domain="helpdesk",
                entity_fqn="entity/helpdesk/ticket",
                base_path="/tickets",
                endpoints=[
                    EndpointIR(method="GET", path="/", summary="List tickets"),
                    EndpointIR(
                        method="POST", path="/", summary="Create ticket", response_status=201
                    ),
                    EndpointIR(method="GET", path="/{id}", summary="Get ticket"),
                    EndpointIR(method="PATCH", path="/{id}", summary="Update ticket"),
                    EndpointIR(
                        method="DELETE",
                        path="/{id}",
                        summary="Delete ticket",
                        response_status=204,
                    ),
                ],
            ),
        ],
    )


class TestGenScaffold:
    def test_generates_package_json(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold

        files = generate_scaffold(helpdesk_ir)
        pkg = next(f for f in files if f.path == "frontend/package.json")
        data = json.loads(pkg.content)
        assert data["name"] == "helpdesk-frontend"
        assert "next" in data["dependencies"]
        assert "react" in data["dependencies"]
        assert "tailwindcss" in data["devDependencies"]

    def test_generates_tailwind_config(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold

        files = generate_scaffold(helpdesk_ir)
        tw = next(f for f in files if f.path == "frontend/tailwind.config.js")
        assert "content" in tw.content
        assert "./src/" in tw.content

    def test_generates_utils(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold

        files = generate_scaffold(helpdesk_ir)
        utils = next(f for f in files if f.path == "frontend/src/lib/utils.ts")
        assert "cn(" in utils.content


def _api_client_files(ir: DomainIR):
    """The api-client bundle: the client itself plus the config it reads."""
    from forge.targets.nextjs.context import FrontendContext
    from forge.targets.nextjs.gen_api_client import generate_api_client

    return generate_api_client(FrontendContext(ir))


def _file(files, path: str):
    return next(f for f in files if f.path == path)


class TestGenAPIClient:
    def test_generates_api_client(self, helpdesk_ir: DomainIR) -> None:
        files = _api_client_files(helpdesk_ir)
        # The base URL lives in config.ts so that session.ts can read it
        # without closing an import cycle with api.ts.
        assert "NEXT_PUBLIC_API_URL" in _file(files, "frontend/src/lib/config.ts").content

        file = _file(files, "frontend/src/lib/api.ts")
        assert file.path == "frontend/src/lib/api.ts"
        assert "export const tickets" in file.content
        assert "list:" in file.content
        assert "create:" in file.content
        assert "get:" in file.content
        assert "update:" in file.content
        assert 'method: "DELETE"' in file.content

    def test_api_client_uses_base_path(self, helpdesk_ir: DomainIR) -> None:
        file = _file(_api_client_files(helpdesk_ir), "frontend/src/lib/api.ts")
        assert "/tickets/" in file.content

    def test_list_is_cursor_paginated_not_offset(self, helpdesk_ir: DomainIR) -> None:
        file = _file(_api_client_files(helpdesk_ir), "frontend/src/lib/api.ts")
        assert "offset=" not in file.content
        assert "next_cursor: string | null" in file.content
        assert 'query.set("cursor", params.cursor)' in file.content
        assert "list: (params?: ListParams)" in file.content

    def test_path_parameters_are_encoded(self, helpdesk_ir: DomainIR) -> None:
        file = _file(_api_client_files(helpdesk_ir), "frontend/src/lib/api.ts")
        assert "encodeURIComponent(id)" in file.content

    def test_no_auth_contract_means_no_session_import(self, helpdesk_ir: DomainIR) -> None:
        file = _file(_api_client_files(helpdesk_ir), "frontend/src/lib/api.ts")
        assert "./session" not in file.content


class TestNextJSGenerator:
    def test_generates_complete_frontend(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        gen = NextJSGenerator()
        files = gen.generate(helpdesk_ir)

        paths = {f.path for f in files}

        # Scaffold
        assert "frontend/package.json" in paths
        assert "frontend/tailwind.config.js" in paths

        # API client
        assert "frontend/src/lib/api.ts" in paths

        # Components
        assert "frontend/src/components/TicketTable.tsx" in paths
        assert "frontend/src/components/TicketForm.tsx" in paths
        assert "frontend/src/components/AppSidebar.tsx" in paths

        # Pages
        assert "frontend/src/app/tickets/page.tsx" in paths
        assert "frontend/src/app/tickets/[id]/page.tsx" in paths
        assert "frontend/src/app/tickets/new/page.tsx" in paths

        # Layout
        assert "frontend/src/app/layout.tsx" in paths
        assert "frontend/src/app/page.tsx" in paths

        # Docker
        assert "frontend/Dockerfile.frontend" in paths

        # Types
        assert "frontend/src/lib/types.ts" in paths

    def test_no_pages_returns_empty(self) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        gen = NextJSGenerator()
        files = gen.generate(DomainIR(domain="empty"))
        assert files == []


def _account_domain(domain: str) -> dict:
    """One `account` entity, page and route, in the named domain."""
    return {
        "entities": [
            EntityIR(
                fqn=f"entity/{domain}/account",
                name="account",
                domain=domain,
                fields=[
                    FieldIR(name="id", type="uuid", computed="uuid"),
                    FieldIR(name="name", type="string", required=True),
                ],
            )
        ],
        "pages": [
            PageIR(
                fqn=f"page/{domain}/accounts",
                name="accounts",
                domain=domain,
                route="/accounts",
                entity_fqn=f"entity/{domain}/account",
                views=[{"type": "table", "columns": ["name"]}],
            )
        ],
        "routes": [
            RouteIR(
                fqn=f"route/{domain}/accounts",
                name="accounts",
                domain=domain,
                entity_fqn=f"entity/{domain}/account",
                base_path="/accounts",
                endpoints=[
                    EndpointIR(method="GET", path="/", summary="List"),
                    EndpointIR(method="GET", path="/{id}", summary="Get"),
                ],
            )
        ],
    }


class TestMultiDomain:
    """Two entities sharing a name across domains must not overwrite each other."""

    @pytest.fixture
    def collided_ir(self) -> DomainIR:
        billing = _account_domain("billing")
        support = _account_domain("support")
        return DomainIR(
            domain="billing",
            domains=["billing", "support"],
            entities=billing["entities"] + support["entities"],
            pages=billing["pages"] + support["pages"],
            routes=billing["routes"] + support["routes"],
        )

    def test_components_and_routes_are_namespaced(self, collided_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        paths = {f.path for f in NextJSGenerator().generate(collided_ir)}
        assert "frontend/src/components/BillingAccountTable.tsx" in paths
        assert "frontend/src/components/SupportAccountTable.tsx" in paths
        assert "frontend/src/app/billing/accounts/page.tsx" in paths
        assert "frontend/src/app/support/accounts/page.tsx" in paths

    def test_no_duplicate_output_paths(self, collided_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        files = NextJSGenerator().generate(collided_ir)
        assert len({f.path for f in files}) == len(files)

    def test_api_exports_are_namespaced(self, collided_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        files = NextJSGenerator().generate(collided_ir)
        api = _file(files, "frontend/src/lib/api.ts")
        assert "export const billing_accounts" in api.content
        assert "export const support_accounts" in api.content

    def test_single_domain_names_are_unprefixed(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        paths = {f.path for f in NextJSGenerator().generate(helpdesk_ir)}
        assert "frontend/src/components/TicketTable.tsx" in paths
        assert "frontend/src/app/tickets/page.tsx" in paths


class TestAuth:
    @pytest.fixture
    def authed_ir(self, helpdesk_ir: DomainIR) -> DomainIR:
        from forge.ir.model import InfraIR

        helpdesk_ir.infra = [
            InfraIR(
                fqn="infra/helpdesk/auth",
                name="auth",
                domain="helpdesk",
                category="auth",
                config={"provider": "jwt", "roles": ["admin", "agent"]},
            )
        ]
        return helpdesk_ir

    def test_client_sends_a_bearer_token(self, authed_ir: DomainIR) -> None:
        api = _file(_api_client_files(authed_ir), "frontend/src/lib/api.ts")
        assert "authorizationHeader()" in api.content
        assert "refreshSession()" in api.content

    def test_401_clears_credentials_and_redirects(self, authed_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        files = NextJSGenerator().generate(authed_ir)
        api = _file(files, "frontend/src/lib/api.ts")
        assert "res.status === 401" in api.content
        assert "endSession()" in api.content

        session = _file(files, "frontend/src/lib/session.ts")
        assert "LOGIN_ROUTE" in session.content
        assert "window.location.assign" in session.content

    def test_access_token_is_never_persisted(self, authed_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        session = _file(NextJSGenerator().generate(authed_ir), "frontend/src/lib/session.ts")
        # localStorage is never acceptable here (the prose says so; assert no
        # code touches it), and the access token lives only in the closure.
        assert "window.localStorage" not in session.content
        assert "let accessToken: string | null = null;" in session.content
        # Only the refresh token is ever written, and only to sessionStorage.
        assert "setItem(REFRESH_KEY, token)" in session.content
        assert "accessToken)" not in session.content.split("function writeRefreshToken")[1]

    def test_login_page_is_generated(self, authed_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        paths = {f.path for f in NextJSGenerator().generate(authed_ir)}
        assert "frontend/src/app/login/page.tsx" in paths
        assert "frontend/src/components/AppShell.tsx" in paths

    def test_sign_out_goes_through_the_logout_endpoint(self, authed_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        session = _file(NextJSGenerator().generate(authed_ir), "frontend/src/lib/session.ts")
        assert "/auth/logout" in session.content
        assert 'credentials: "include"' in session.content
        # The tab-scoped signed-out marker existed only because nothing could
        # clear the httpOnly cookie. /auth/logout can, so it is gone.
        assert "signedout" not in session.content
        assert "SIGNED_OUT_KEY" not in session.content

    def test_failed_sign_out_does_not_claim_success(self, authed_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        files = NextJSGenerator().generate(authed_ir)
        session = _file(files, "frontend/src/lib/session.ts")
        sidebar = _file(files, "frontend/src/components/AppSidebar.tsx")
        # Local state is cleared and the user redirected only after the server
        # confirms; otherwise the cookie is still live and the next navigation
        # would silently sign them back in.
        assert "export async function signOut(): Promise<boolean>" in session.content
        assert "Sign-out did not complete" in sidebar.content

    def test_open_redirect_is_rejected(self, authed_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        session = _file(NextJSGenerator().generate(authed_ir), "frontend/src/lib/session.ts")
        assert 'raw.startsWith("//")' in session.content


class TestImmutableFields:
    """`immutable` means unchangeable after creation, not unsettable."""

    @pytest.fixture
    def append_only_ir(self, helpdesk_ir: DomainIR) -> DomainIR:
        entity = helpdesk_ir.entities[0]
        entity.fields = [
            FieldIR(name="id", type="uuid", computed="uuid"),
            FieldIR(name="subject", type="string", required=True, immutable=True),
            FieldIR(name="actor", type="string", required=True, immutable=True),
        ]
        helpdesk_ir.pages[0].views = [{"type": "table", "columns": ["subject"]}]
        return helpdesk_ir

    def test_immutable_fields_appear_on_the_create_form(self, append_only_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        form = _file(
            NextJSGenerator().generate(append_only_ir),
            "frontend/src/components/TicketForm.tsx",
        )
        # An all-immutable entity used to produce a form with no inputs at all.
        assert 'name="subject"' in form.content
        assert 'name="actor"' in form.content
        # ...and each is gated off the edit form, where it cannot be changed.
        assert form.content.count("{!isEdit && (") == 2

    def test_no_edit_page_when_nothing_is_updatable(self, append_only_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        files = NextJSGenerator().generate(append_only_ir)
        paths = {f.path for f in files}
        assert "frontend/src/app/tickets/new/page.tsx" in paths
        assert "frontend/src/app/tickets/[id]/edit/page.tsx" not in paths
        # ...and the detail page must not offer an Edit button leading nowhere.
        detail = _file(files, "frontend/src/app/tickets/[id]/page.tsx")
        assert "/edit`" not in detail.content

    def test_mutable_field_stays_on_both_forms(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        files = NextJSGenerator().generate(helpdesk_ir)
        form = _file(files, "frontend/src/components/TicketForm.tsx")
        assert 'name="subject"' in form.content
        assert "{!isEdit && (" not in form.content
        assert "frontend/src/app/tickets/[id]/edit/page.tsx" in {f.path for f in files}


class TestContractDefectsFailLoudly:
    def test_column_naming_a_missing_field_is_rejected(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.base import GenerationError
        from forge.targets.nextjs.generator import NextJSGenerator

        helpdesk_ir.pages[0].views = [{"type": "table", "columns": ["subject", "phone"]}]
        with pytest.raises(GenerationError, match="phone"):
            NextJSGenerator().generate(helpdesk_ir)

    def test_write_only_field_is_kept_out_of_read_views(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        helpdesk_ir.entities[0].fields.append(
            FieldIR(name="api_secret", type="string", required=True, sensitive=True)
        )

        files = NextJSGenerator().generate(helpdesk_ir)
        detail = _file(files, "frontend/src/components/TicketDetail.tsx")
        form = _file(files, "frontend/src/components/TicketForm.tsx")
        assert "api_secret" not in detail.content
        # Still writable: it belongs on the create/edit form.
        assert 'name="api_secret"' in form.content
        assert 'type="password"' in form.content


class TestFrontendIsReproducible:
    """The dependency graph must be a function of the contracts, not of the clock.

    `package.json` used to declare caret ranges and ship no lockfile, so the
    `npm install` inside Dockerfile.frontend re-resolved 143 packages on every
    build. Two builds of identical contracts could ship different code.
    """

    def test_no_dependency_is_a_range(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold

        pkg = json.loads(_file(generate_scaffold(helpdesk_ir), "frontend/package.json").content)
        declared = {**pkg["dependencies"], **pkg["devDependencies"]}
        assert declared, "the frontend must declare dependencies"
        ranged = {n: v for n, v in declared.items() if not v[0].isdigit()}
        assert ranged == {}, f"these are ranges, not versions: {ranged}"

    def test_a_lockfile_is_emitted(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_scaffold import generate_scaffold

        files = generate_scaffold(helpdesk_ir)
        lock = json.loads(_file(files, "frontend/package-lock.json").content)
        assert lock["lockfileVersion"] >= 3
        # Named for the project it sits in, not for the template it came from.
        assert lock["name"] == "helpdesk-frontend"
        assert lock["packages"][""]["name"] == "helpdesk-frontend"

    def test_every_locked_package_carries_an_integrity_hash(self, helpdesk_ir: DomainIR) -> None:
        """A pinned version without a hash still trusts the registry's bytes."""
        from forge.targets.nextjs.gen_scaffold import generate_scaffold

        files = generate_scaffold(helpdesk_ir)
        lock = json.loads(_file(files, "frontend/package-lock.json").content)
        unhashed = [k for k, v in lock["packages"].items() if k and not v.get("integrity")]
        assert unhashed == []
        assert len(lock["packages"]) > 100, "the point is the transitive tree, not the 13 direct"

    def test_lockfile_and_manifest_agree(self, helpdesk_ir: DomainIR) -> None:
        """`npm ci` aborts when they disagree, so disagreement must not generate."""
        from forge.targets.nextjs.gen_scaffold import generate_scaffold

        files = generate_scaffold(helpdesk_ir)
        pkg = json.loads(_file(files, "frontend/package.json").content)
        lock = json.loads(_file(files, "frontend/package-lock.json").content)
        for group in ("dependencies", "devDependencies"):
            assert lock["packages"][""][group] == pkg[group]
            for name, version in pkg[group].items():
                assert lock["packages"][f"node_modules/{name}"]["version"] == version

    def test_a_lockfile_that_drifted_from_the_pins_fails_generation(self) -> None:
        from forge.targets.base import GenerationError
        from forge.targets.nextjs import npm_deps

        stale = json.loads(npm_deps._LOCKFILE.read_text(encoding="utf-8"))
        stale["packages"]["node_modules/next"]["version"] = "0.0.1"
        with pytest.raises(GenerationError, match="regen_frontend_lock"):
            npm_deps._verify(stale)

    def test_a_lockfile_missing_an_integrity_hash_fails_generation(self) -> None:
        from forge.targets.base import GenerationError
        from forge.targets.nextjs import npm_deps

        stripped = json.loads(npm_deps._LOCKFILE.read_text(encoding="utf-8"))
        del stripped["packages"]["node_modules/next"]["integrity"]
        with pytest.raises(GenerationError, match="integrity"):
            npm_deps._verify(stripped)

    def test_dockerfile_installs_from_the_lockfile(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.generator import NextJSGenerator

        dockerfile = _file(
            NextJSGenerator().generate(helpdesk_ir), "frontend/Dockerfile.frontend"
        ).content
        # The RUN lines, not the comments around them — the comments name
        # `npm install` precisely to say why it is not used.
        runs = [ln for ln in dockerfile.splitlines() if ln.startswith("RUN ") and "npm" in ln]
        install = next(ln for ln in runs if " ci" in ln or " install" in ln)
        # `npm install` would re-resolve and silently rewrite the lockfile;
        # `npm ci` installs it exactly and fails if it is absent or has drifted.
        assert "npm ci" in install
        assert "npm install" not in install
        # Otherwise the install runs lifecycle scripts from 143 packages as root.
        assert "--ignore-scripts" in install
        # ci is only reproducible if the lockfile actually reaches the image.
        assert "COPY package.json package-lock.json ./" in dockerfile


def _referencing_ir() -> DomainIR:
    """An `entry` entity with two columns pointing at the same `account`."""
    from forge.ir.model import ReferenceIR

    account = EntityIR(
        fqn="entity/ledger/account",
        name="account",
        domain="ledger",
        table_name="accounts",
        fields=[
            FieldIR(name="id", type="uuid", computed="uuid"),
            FieldIR(name="name", type="string", required=True),
        ],
    )
    entry = EntityIR(
        fqn="entity/ledger/entry",
        name="entry",
        domain="ledger",
        table_name="entries",
        fields=[
            FieldIR(name="id", type="uuid", computed="uuid"),
            FieldIR(
                name="debit_account_id",
                type="uuid",
                reference=ReferenceIR(target_entity="entity/ledger/account", display_field="name"),
            ),
            FieldIR(
                name="credit_account_id",
                type="uuid",
                reference=ReferenceIR(target_entity="entity/ledger/account", display_field="name"),
            ),
        ],
    )
    page = PageIR(
        fqn="page/ledger/entries",
        name="entries",
        domain="ledger",
        route="/entries",
        entity_fqn="entity/ledger/entry",
        views=[
            {
                "type": "table",
                "columns": ["debit_account_id", "credit_account_id"],
            }
        ],
    )
    routes = [
        RouteIR(
            fqn=f"route/ledger/{name}s",
            name=f"{name}s",
            domain="ledger",
            entity_fqn=f"entity/ledger/{name}",
            base_path=f"/{name}s",
            endpoints=[
                EndpointIR(method="GET", path="/", summary="List"),
                EndpointIR(method="GET", path="/{id}", summary="Get"),
            ],
        )
        for name in ("account", "entry")
    ]
    return DomainIR(domain="ledger", entities=[account, entry], pages=[page], routes=routes)


def _components(ir: DomainIR):
    from forge.targets.nextjs.context import FrontendContext
    from forge.targets.nextjs.gen_components import generate_components

    return generate_components(FrontendContext(ir))


class TestReferenceResolution:
    """Pins: display names were resolved from the referenced collection's first
    page, so any id outside it rendered as `unresolved` forever — and looked
    correct in every fixture small enough to fit in one page."""

    def test_the_lookup_asks_for_the_ids_on_screen(self) -> None:
        table = _file(_components(_referencing_ir()), "frontend/src/components/EntryTable.tsx")

        assert "referenceKey(" in table.content
        assert ".list({ limit: ids.length, ids })" in table.content
        assert "limit: 200" not in table.content

    def test_two_columns_on_one_target_share_a_lookup_that_covers_both(self) -> None:
        """A single lookup built from only the first column leaves the second
        rendering against a map that never contained its ids."""
        table = _file(_components(_referencing_ir()), "frontend/src/components/EntryTable.tsx")

        assert (
            "referenceKey(items.flatMap((item) => "
            "[item.debit_account_id, item.credit_account_id]))" in table.content
        )
        assert table.content.count("const accountKey = useMemo(") == 1

    def test_the_effect_reruns_only_when_the_ids_change(self) -> None:
        """Depending on the rows themselves refetches on every render that
        hands the component an equal-but-new array."""
        table = _file(_components(_referencing_ir()), "frontend/src/components/EntryTable.tsx")

        assert "}, [accountKey]);" in table.content
        assert "}, []);" not in table.content

    def test_the_detail_view_resolves_its_own_record(self) -> None:
        detail = _file(_components(_referencing_ir()), "frontend/src/components/EntryDetail.tsx")

        assert "referenceKey([data.debit_account_id, data.credit_account_id])" in detail.content

    def test_an_entity_with_no_references_imports_no_hooks(self, helpdesk_ir: DomainIR) -> None:
        """Generated modules import exactly what they use."""
        table = _file(_components(helpdesk_ir), "frontend/src/components/TicketTable.tsx")

        assert "useEffect" not in table.content
        assert "referenceKey" not in table.content

    def test_the_form_picker_still_loads_a_page_of_choices(self) -> None:
        """A picker has to offer choices the record does not yet point at, so
        it is the one place a page of the target is still the right request."""
        from forge.targets.nextjs.gen_components import REFERENCE_OPTIONS_LIMIT

        form = _file(_components(_referencing_ir()), "frontend/src/components/EntryForm.tsx")

        assert f".list({{ limit: {REFERENCE_OPTIONS_LIMIT} }})" in form.content


class TestBatchLookupClient:
    def test_the_client_sends_one_parameter_per_id(self, helpdesk_ir: DomainIR) -> None:
        """Repeated parameters, not a delimited value: an identifier is opaque
        and could contain whatever separator was chosen."""
        api = _file(_api_client_files(helpdesk_ir), "frontend/src/lib/api.ts")

        assert 'query.append("id__in", String(id))' in api.content
        assert "ids?: readonly string[];" in api.content

    def test_the_client_bounds_the_batch_at_the_servers_own_ceiling(
        self, helpdesk_ir: DomainIR
    ) -> None:
        from forge.targets.fastapi_prod.gen_routes import MAX_FILTER_IDS

        files = _api_client_files(helpdesk_ir)
        config = _file(files, "frontend/src/lib/config.ts")
        api = _file(files, "frontend/src/lib/api.ts")

        assert f"export const MAX_LOOKUP_IDS = {MAX_FILTER_IDS};" in config.content
        assert ".slice(0, MAX_LOOKUP_IDS)" in api.content

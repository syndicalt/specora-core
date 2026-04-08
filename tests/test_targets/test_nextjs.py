"""Tests for the Next.js frontend generator."""
import json

import pytest

from forge.ir.model import DomainIR, EntityIR, FieldIR, PageIR, RouteIR, EndpointIR


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
                    FieldIR(name="priority", type="string", enum_values=["critical", "high", "medium", "low"]),
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
                    EndpointIR(method="POST", path="/", summary="Create ticket", response_status=201),
                    EndpointIR(method="GET", path="/{id}", summary="Get ticket"),
                    EndpointIR(method="PATCH", path="/{id}", summary="Update ticket"),
                    EndpointIR(method="DELETE", path="/{id}", summary="Delete ticket", response_status=204),
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


class TestGenAPIClient:

    def test_generates_api_client(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_api_client import generate_api_client
        file = generate_api_client(helpdesk_ir)
        assert file.path == "frontend/src/lib/api.ts"
        assert "NEXT_PUBLIC_API_URL" in file.content
        assert "export const tickets" in file.content
        assert "list:" in file.content
        assert "create:" in file.content
        assert "get:" in file.content
        assert "update:" in file.content
        assert 'method: "DELETE"' in file.content

    def test_api_client_uses_base_path(self, helpdesk_ir: DomainIR) -> None:
        from forge.targets.nextjs.gen_api_client import generate_api_client
        file = generate_api_client(helpdesk_ir)
        assert "/tickets/" in file.content


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

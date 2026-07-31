"""Tests for extractor.analyzers.routes — AST route discovery."""

from pathlib import Path

from extractor.analyzers.routes import analyze_routes


def _analyze(tmp_path: Path, source: str, name: str = "routes.py"):
    (tmp_path / name).write_text(source, encoding="utf-8")
    warnings: list[str] = []
    routes = analyze_routes([name], tmp_path, warnings=warnings)
    return routes, warnings


class TestFastAPI:
    def test_router_prefix_is_joined_onto_the_path(self, tmp_path: Path) -> None:
        routes, _ = _analyze(
            tmp_path,
            """
from fastapi import APIRouter

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("/", summary="List tickets")
async def list_tickets():
    ...


@router.patch("/{record_id}")
async def update_ticket(record_id: str):
    "Update a ticket."
""",
        )
        by_method = {r.method: r for r in routes}
        assert by_method["GET"].path == "/tickets"
        assert by_method["GET"].summary == "List tickets"
        assert by_method["PATCH"].path == "/tickets/{record_id}"
        assert by_method["PATCH"].summary == "Update a ticket."

    def test_entity_name_ignores_path_parameters(self, tmp_path: Path) -> None:
        """`/{record_id}` used to be reported as an entity called `{record_id}`."""
        routes, _ = _analyze(
            tmp_path,
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/v1/tickets')\n"
            "@router.get('/{record_id}')\n"
            "def get(record_id: str): ...\n",
        )
        assert [r.entity_name for r in routes] == ["ticket"]

    def test_bare_path_with_no_resource_yields_no_entity(self, tmp_path: Path) -> None:
        routes, _ = _analyze(
            tmp_path,
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/{record_id}')\n"
            "def get(record_id: str): ...\n",
        )
        assert [r.entity_name for r in routes] == [""]


class TestFlask:
    def test_blueprint_prefix_and_methods(self, tmp_path: Path) -> None:
        routes, _ = _analyze(
            tmp_path,
            """
from flask import Blueprint

bp = Blueprint("books", __name__, url_prefix="/books")


@bp.route("/", methods=["GET", "POST"])
def index():
    ...
""",
        )
        assert {(r.method, r.path) for r in routes} == {("GET", "/books"), ("POST", "/books")}


class TestFalsePositives:
    def test_ordinary_method_calls_are_not_endpoints(self, tmp_path: Path) -> None:
        """The old regex matched any `.get(` — `requests.get` became a route."""
        routes, _ = _analyze(
            tmp_path,
            "import requests\n"
            "def fetch(config):\n"
            "    requests.get('https://example.com/users')\n"
            "    return config.get('timeout')\n",
        )
        assert routes == []

    def test_api_view_is_reported_rather_than_invented(self, tmp_path: Path) -> None:
        """`@api_view(["GET"])` used to yield a route whose path was 'GET'."""
        routes, warnings = _analyze(
            tmp_path,
            "from rest_framework.decorators import api_view\n"
            "@api_view(['GET', 'POST'])\n"
            "def listing(request): ...\n",
        )
        assert routes == []
        assert any("api_view" in w for w in warnings)

    def test_duplicate_registrations_are_collapsed(self, tmp_path: Path) -> None:
        routes, _ = _analyze(
            tmp_path,
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/books')\n"
            "def a(): ...\n"
            "@router.get('/books')\n"
            "def b(): ...\n",
        )
        assert len(routes) == 1


class TestJavaScript:
    def test_string_literal_paths_are_extracted(self, tmp_path: Path) -> None:
        routes, _ = _analyze(
            tmp_path,
            "const router = express.Router();\nrouter.post('/books', createBook);\n",
            name="controller.ts",
        )
        assert [(r.method, r.path) for r in routes] == [("POST", "/books")]

    def test_computed_paths_are_reported_as_a_gap(self, tmp_path: Path) -> None:
        routes, warnings = _analyze(
            tmp_path,
            "router.get(BOOKS_PATH, listBooks);\n",
            name="controller.ts",
        )
        assert routes == []
        assert any("computed path" in w for w in warnings)


class TestHostileInput:
    def test_unparseable_python_is_reported(self, tmp_path: Path) -> None:
        routes, warnings = _analyze(tmp_path, "@router.get('/x'\ndef broken(: ...\n")
        assert routes == []
        assert any("not parseable as Python" in w for w in warnings)

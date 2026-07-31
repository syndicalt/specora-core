"""End-to-end proof that emitter output is usable, not just well-shaped.

`test_emitters.py` asserts on the parsed YAML. That cannot catch a contract
that validates and then fails the compiler's semantic pass, or one that
compiles and then fails a generator — both of which the emitters could produce.
These tests run the real pipeline: emit -> validate_all -> compile -> generate
with every target CI builds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.emitters.base import EmitterError
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.page_emitter import emit_page, page_columns
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from forge.ir.compiler import Compiler
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all
from forge.targets.fastapi_prod.generator import (
    DockerGenerator,
    FastAPIProductionGenerator,
    TestSuiteGenerator,
)
from forge.targets.nextjs.generator import NextJSGenerator
from forge.targets.postgres.gen_ddl import PostgresGenerator
from forge.targets.typescript.gen_types import TypeScriptGenerator

# The `prod` preset, matching scripts/ci_generate_all.py.
GENERATORS = [
    FastAPIProductionGenerator,
    PostgresGenerator,
    DockerGenerator,
    TypeScriptGenerator,
    TestSuiteGenerator,
    NextJSGenerator,
]


def build_and_generate(tmp_path: Path, files: dict[str, str]):
    """Write contracts, validate, compile, and run every production target."""
    for rel_path, content in files.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    errors = validate_all(load_all_contracts(tmp_path))
    assert errors == [], [(e.contract_fqn, e.path, e.message) for e in errors]

    ir = Compiler(contract_root=tmp_path).compile()

    produced: dict[str, str] = {}
    for generator in GENERATORS:
        for generated in generator().generate(ir):
            produced[generated.path] = generated.content
    assert produced
    return ir, produced


def full_domain(domain: str = "shop") -> dict[str, str]:
    """Every emitter, exercising the current contract language."""
    entity_fields = {
        "name": {"type": "string", "required": True, "description": "Product name"},
        "price": {
            "type": "decimal",
            "required": True,
            "description": "Unit price",
            "constraints": {"precision": 12, "scale": 2},
        },
        "rating": {"type": "number", "description": "Average rating"},
        "api_secret": {"type": "string", "sensitive": True, "description": "Vendor API secret"},
        "category": {"type": "string", "enum": ["electronics", "food"]},
        "vendor_id": {
            "type": "uuid",
            "references": {
                "entity": f"entity/{domain}/vendor",
                "display": "name",
                "graph_edge": "SOLD_BY",
            },
        },
    }
    vendor_fields = {"name": {"type": "string", "required": True}}

    workflow_data = {
        "initial": "draft",
        "states": {
            "draft": {"label": "Draft", "category": "open"},
            "listed": {"label": "Listed", "category": "open"},
            "withdrawn": {"label": "Withdrawn", "category": "closed", "terminal": True},
        },
        "transitions": {"draft": ["listed"], "listed": ["withdrawn"]},
        "guards": {"draft -> listed": {"require_fields": ["price"]}},
        "description": "Product listing lifecycle",
    }

    workflow_fqn = f"workflow/{domain}/product_lifecycle"
    product_fqn = f"entity/{domain}/product"
    vendor_fqn = f"entity/{domain}/vendor"

    return {
        "entities/product.contract.yaml": emit_entity(
            "product",
            domain,
            {
                "description": "A product for sale",
                "fields": entity_fields,
                "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
                "state_machine": workflow_fqn,
                "number_prefix": "PRD",
                "icon": "package",
            },
        ),
        "entities/vendor.contract.yaml": emit_entity(
            "vendor",
            domain,
            {
                "description": "A vendor",
                "fields": vendor_fields,
                "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
            },
        ),
        "workflows/product_lifecycle.contract.yaml": emit_workflow(
            "product_lifecycle", domain, workflow_data
        ),
        "routes/products.contract.yaml": emit_route("products", domain, product_fqn, workflow_fqn),
        "routes/vendors.contract.yaml": emit_route("vendors", domain, vendor_fqn),
        "pages/products.contract.yaml": emit_page(
            "products", domain, product_fqn, page_columns(entity_fields)
        ),
        "pages/vendors.contract.yaml": emit_page(
            "vendors", domain, vendor_fqn, page_columns(vendor_fields)
        ),
    }


class TestEmittedDomainGenerates:
    def test_every_emitter_survives_the_full_pipeline(self, tmp_path: Path) -> None:
        ir, produced = build_and_generate(tmp_path, full_domain())

        assert {e.name for e in ir.entities} == {"product", "vendor"}
        assert len(ir.routes) == 2
        assert len(ir.pages) == 2
        assert "backend/app.py" in produced
        assert "database/schema.sql" in produced

    def test_decimal_reaches_the_database_as_numeric(self, tmp_path: Path) -> None:
        _, produced = build_and_generate(tmp_path, full_domain())
        assert "NUMERIC(12, 2)" in produced["database/schema.sql"]

    def test_sensitive_field_never_reaches_the_table_view(self, tmp_path: Path) -> None:
        # page_columns keeps the secret out of the page contract, so the
        # frontend table never names it. Naming it would be a GenerationError.
        page_yaml = full_domain()["pages/products.contract.yaml"]
        assert "api_secret" not in page_yaml

        _, produced = build_and_generate(tmp_path, full_domain())
        assert "api_secret" not in produced["frontend/src/components/ProductTable.tsx"]
        assert (
            '_SENSITIVE_FIELDS = frozenset({"api_secret"})' in produced["backend/routes_product.py"]
        )

    def test_workflow_binding_produces_a_state_endpoint(self, tmp_path: Path) -> None:
        _, produced = build_and_generate(tmp_path, full_domain())
        assert '@router.put("/{record_id}/state"' in produced["backend/routes_product.py"]


class TestEmitterRejectsBadInput:
    @pytest.mark.parametrize(
        "name", ["../../../evil", "..", "a/b", "", "1abc", "café", "SELECT * FROM x"]
    )
    def test_entity_name_that_cannot_validate_is_refused(self, name: str) -> None:
        with pytest.raises(EmitterError):
            emit_entity(name, "shop", {"fields": {"a": {"type": "string"}}})

    @pytest.mark.parametrize("domain", ["../../etc", "..", "Bad Domain"])
    def test_domain_that_cannot_validate_is_refused(self, domain: str) -> None:
        with pytest.raises(EmitterError):
            emit_entity("thing", domain, {"fields": {"a": {"type": "string"}}})

    def test_unknown_field_property_is_refused(self) -> None:
        # The meta-schemas reject unknown keys; an LLM inventing `maxLength` at
        # field level must not reach disk.
        with pytest.raises(EmitterError, match="maxLength"):
            emit_entity("thing", "shop", {"fields": {"a": {"type": "string", "maxLength": 5}}})

    def test_unknown_field_type_is_refused(self) -> None:
        with pytest.raises(EmitterError):
            emit_entity("thing", "shop", {"fields": {"a": {"type": "money"}}})

    def test_scalar_field_definition_is_refused_with_the_field_named(self) -> None:
        # `name: string` is the shape an LLM produces most often; it used to
        # raise AttributeError from inside the reference scan.
        with pytest.raises(EmitterError, match="'a'"):
            emit_entity("thing", "shop", {"fields": {"a": "string"}})

    def test_provably_empty_entity_is_refused(self) -> None:
        with pytest.raises(EmitterError, match="no fields"):
            emit_entity("thing", "shop", {"fields": {}, "mixins": []})

    def test_entity_with_only_mixins_is_allowed(self) -> None:
        # Mixin fields are resolved by the compiler, so this one is not empty.
        assert emit_entity("thing", "shop", {"fields": {}, "mixins": ["mixin/stdlib/identifiable"]})

    def test_workflow_with_undeclared_initial_state_is_refused(self) -> None:
        with pytest.raises(EmitterError, match="initial state"):
            emit_workflow(
                "lc",
                "shop",
                {"initial": "nope", "states": {"a": {"label": "A"}}, "transitions": {}},
            )

    def test_workflow_with_undeclared_transition_target_is_refused(self) -> None:
        with pytest.raises(EmitterError, match="undeclared state"):
            emit_workflow(
                "lc",
                "shop",
                {
                    "initial": "a",
                    "states": {"a": {"label": "A"}},
                    "transitions": {"a": ["vanished"]},
                },
            )

    def test_workflow_missing_a_required_key_is_refused(self) -> None:
        with pytest.raises(EmitterError, match="transitions"):
            emit_workflow("lc", "shop", {"initial": "a", "states": {"a": {"label": "A"}}})

    @pytest.mark.parametrize("name", ["../../evil", "", "a/b"])
    def test_page_and_route_names_are_refused(self, name: str) -> None:
        with pytest.raises(EmitterError):
            emit_page(name, "shop", "entity/shop/product", ["name"])
        with pytest.raises(EmitterError):
            emit_route(name, "shop", "entity/shop/product")


class TestRouteSummaries:
    @pytest.mark.parametrize(
        ("route_name", "entity_name"),
        [("addresses", "address"), ("classes", "class_room"), ("statuses", "status")],
    )
    def test_singular_comes_from_the_entity_not_from_rstrip(
        self, route_name: str, entity_name: str
    ) -> None:
        # `route_name.rstrip("s")` produced "addresse" / "statu".
        yaml_str = emit_route(route_name, "shop", f"entity/shop/{entity_name}")
        assert f"Create a new {entity_name}" in yaml_str

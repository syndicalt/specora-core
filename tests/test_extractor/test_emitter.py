"""Tests for extractor.emitter — AnalysisReport to contract YAML.

The load-bearing test here is `test_emitted_domain_validates_and_compiles`.
The Extractor's whole purpose is producing contracts that become somebody's
source of truth, so "it wrote a file" is not the property worth asserting.
"""

from pathlib import Path

import pytest
import yaml

from extractor.emitter import EmissionError, emit_contracts
from extractor.models import (
    AnalysisReport,
    ExtractedEntity,
    ExtractedField,
    ExtractedWorkflow,
)
from forge.ir.compiler import Compiler
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all


def _shop_report() -> AnalysisReport:
    return AnalysisReport(
        domain="shop",
        entities=[
            ExtractedEntity(
                name="customer",
                source_file="models.py",
                description="A buyer",
                fields=[ExtractedField(name="name", type="string", required=True)],
            ),
            ExtractedEntity(
                name="order",
                source_file="models.py",
                description="A placed order",
                fields=[
                    ExtractedField(name="total", type="decimal", required=True),
                    ExtractedField(
                        name="customer_id",
                        type="uuid",
                        required=True,
                        reference_entity="entity/shop/customer",
                        reference_edge="PLACED_BY",
                    ),
                ],
                state_field="status",
                state_values=["pending", "shipped"],
            ),
        ],
        workflows=[
            ExtractedWorkflow(
                name="order_lifecycle",
                entity_name="order",
                states=["pending", "shipped"],
                initial="pending",
                source_file="models.py",
            ),
        ],
    )


class TestEmitContracts:
    def test_emits_entity_contracts(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(
                    name="product",
                    source_file="models.py",
                    description="A product",
                    fields=[
                        ExtractedField(name="name", type="string", required=True),
                        ExtractedField(name="price", type="number"),
                    ],
                ),
            ],
        )
        files = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        entity_file = tmp_path / "domains" / "shop" / "entities" / "product.contract.yaml"
        assert entity_file in files
        assert entity_file.exists()

        contract = yaml.safe_load(entity_file.read_text(encoding="utf-8"))
        assert contract["kind"] == "Entity"
        assert contract["metadata"]["name"] == "product"
        assert contract["spec"]["fields"]["price"]["type"] == "number"
        assert contract["spec"]["fields"]["name"]["required"] is True

    def test_emits_route_and_page(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(
                    name="product",
                    source_file="m.py",
                    fields=[ExtractedField(name="name", type="string")],
                ),
            ],
        )
        written = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        route_file = tmp_path / "domains" / "shop" / "routes" / "products.contract.yaml"
        page_file = tmp_path / "domains" / "shop" / "pages" / "products.contract.yaml"
        assert {route_file, page_file} <= set(written)

        route = yaml.safe_load(route_file.read_text(encoding="utf-8"))
        assert route["spec"]["entity"] == "entity/shop/product"
        page = yaml.safe_load(page_file.read_text(encoding="utf-8"))
        assert page["spec"]["entity"] == "entity/shop/product"
        assert page["spec"]["views"][0]["columns"] == ["name"]

    def test_emits_workflow(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(
                    name="order",
                    source_file="m.py",
                    fields=[ExtractedField(name="total", type="number")],
                )
            ],
            workflows=[
                ExtractedWorkflow(
                    name="order_lifecycle",
                    entity_name="order",
                    states=["pending", "shipped", "delivered"],
                    initial="pending",
                    source_file="m.py",
                ),
            ],
        )
        written = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        wf_file = tmp_path / "domains" / "shop" / "workflows" / "order_lifecycle.contract.yaml"
        assert wf_file in set(written)

        workflow = yaml.safe_load(wf_file.read_text(encoding="utf-8"))
        assert workflow["spec"]["transitions"] == {
            "pending": ["shipped"],
            "shipped": ["delivered"],
        }
        entity = yaml.safe_load(
            (tmp_path / "domains" / "shop" / "entities" / "order.contract.yaml").read_text()
        )
        assert entity["spec"]["state_machine"] == "workflow/shop/order_lifecycle"

    def test_emitted_domain_validates_and_compiles(self, tmp_path: Path) -> None:
        out = tmp_path / "domains" / "shop"
        emit_contracts(_shop_report(), output_dir=out)

        errors = validate_all(load_all_contracts(out))
        assert [e.message for e in errors if e.severity == "error"] == []

        ir = Compiler(contract_root=out).compile()
        assert {e.name for e in ir.entities} == {"customer", "order"}

    def test_reference_to_a_skipped_entity_is_dropped(self, tmp_path: Path) -> None:
        """A user who skips `customer` must not get a domain that cannot compile.

        `emit_entity` copies every `references.entity` into `requires`, and the
        compiler rejects a `requires` pointing at a contract that was not
        written.
        """
        report = _shop_report()
        only_order = [e for e in report.entities if e.name == "order"]
        out = tmp_path / "domains" / "shop"
        emit_contracts(report, output_dir=out, accepted_entities=only_order)

        contract = yaml.safe_load((out / "entities" / "order.contract.yaml").read_text())
        assert "references" not in contract["spec"]["fields"]["customer_id"]
        assert "entity/shop/customer" not in contract["requires"]

        assert [e for e in validate_all(load_all_contracts(out)) if e.severity == "error"] == []
        Compiler(contract_root=out).compile()

    def test_orphan_workflow_is_not_written(self, tmp_path: Path) -> None:
        report = _shop_report()
        out = tmp_path / "domains" / "shop"
        emit_contracts(
            report,
            output_dir=out,
            accepted_entities=[e for e in report.entities if e.name == "customer"],
        )
        assert not (out / "workflows").exists()

    def test_name_cannot_escape_the_output_directory(self, tmp_path: Path) -> None:
        """A class name reaches the emitter straight from the scanned codebase."""
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(
                    name="../../../../etc/passwd",
                    source_file="m.py",
                    fields=[ExtractedField(name="name", type="string")],
                ),
            ],
        )
        out = tmp_path / "domains" / "shop"
        written = emit_contracts(report, output_dir=out)

        assert written
        for path in written:
            assert path.is_relative_to(out.resolve())
        assert not (tmp_path.parent / "etc").exists()

    def test_colliding_collection_names_do_not_overwrite(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(
                    name="bus", source_file="m.py", fields=[ExtractedField(name="a", type="string")]
                ),
                ExtractedEntity(
                    name="buse",
                    source_file="m.py",
                    fields=[ExtractedField(name="b", type="string")],
                ),
            ],
        )
        out = tmp_path / "domains" / "shop"
        written = emit_contracts(report, output_dir=out)
        routes = [p for p in written if p.parent.name == "routes"]
        assert len(routes) == len({p.name for p in routes}) == 2

    def test_invalid_contract_is_not_written(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(
                    name="widget",
                    source_file="m.py",
                    fields=[ExtractedField(name="size", type="furlong")],
                ),
            ],
        )
        out = tmp_path / "domains" / "shop"
        with pytest.raises(EmissionError, match="furlong"):
            emit_contracts(report, output_dir=out)
        assert not (out / "entities" / "widget.contract.yaml").exists()

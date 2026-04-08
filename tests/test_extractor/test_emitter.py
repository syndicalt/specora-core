"""Tests for extractor.emitter — AnalysisReport to contract YAML."""
from pathlib import Path

import pytest
import yaml

from extractor.emitter import emit_contracts
from extractor.models import (
    AnalysisReport,
    ExtractedEntity,
    ExtractedField,
    ExtractedWorkflow,
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

        assert len(files) >= 1
        entity_file = tmp_path / "domains" / "shop" / "entities" / "product.contract.yaml"
        assert entity_file.exists()

        contract = yaml.safe_load(entity_file.read_text(encoding="utf-8"))
        assert contract["kind"] == "Entity"
        assert contract["metadata"]["name"] == "product"

    def test_emits_route_and_page(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(name="product", source_file="m.py", fields=[
                    ExtractedField(name="name", type="string"),
                ]),
            ],
        )
        files = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        route_file = tmp_path / "domains" / "shop" / "routes" / "products.contract.yaml"
        assert route_file.exists()

        page_file = tmp_path / "domains" / "shop" / "pages" / "products.contract.yaml"
        assert page_file.exists()

    def test_emits_workflow(self, tmp_path: Path) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[ExtractedEntity(name="order", source_file="m.py", fields=[])],
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
        files = emit_contracts(report, output_dir=tmp_path / "domains" / "shop")

        wf_file = tmp_path / "domains" / "shop" / "workflows" / "order_lifecycle.contract.yaml"
        assert wf_file.exists()

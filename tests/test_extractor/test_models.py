"""Tests for extractor.models — data structures for codebase analysis."""
import pytest

from extractor.models import (
    AnalysisReport,
    Confidence,
    ExtractedEntity,
    ExtractedField,
    ExtractedRoute,
    ExtractedWorkflow,
    FileClassification,
    FileRole,
)


class TestFileClassification:

    def test_create(self) -> None:
        fc = FileClassification(path="models.py", role=FileRole.MODEL, language="python")
        assert fc.path == "models.py"
        assert fc.role == FileRole.MODEL
        assert fc.language == "python"


class TestExtractedEntity:

    def test_create_minimal(self) -> None:
        entity = ExtractedEntity(
            name="User",
            source_file="models.py",
            fields=[ExtractedField(name="email", type="string", required=True)],
        )
        assert entity.name == "User"
        assert len(entity.fields) == 1
        assert entity.confidence == Confidence.HIGH

    def test_to_emitter_data(self) -> None:
        entity = ExtractedEntity(
            name="Book",
            source_file="models.py",
            description="A library book",
            fields=[
                ExtractedField(name="title", type="string", required=True, description="Book title"),
                ExtractedField(name="isbn", type="string"),
            ],
        )
        data = entity.to_emitter_data()
        assert data["description"] == "A library book"
        assert "title" in data["fields"]
        assert data["fields"]["title"]["type"] == "string"
        assert data["fields"]["title"]["required"] is True
        assert "mixin/stdlib/timestamped" in data["mixins"]


class TestExtractedRoute:

    def test_create(self) -> None:
        route = ExtractedRoute(
            path="/api/users",
            method="GET",
            entity_name="user",
            source_file="routes.py",
        )
        assert route.path == "/api/users"


class TestExtractedWorkflow:

    def test_create(self) -> None:
        wf = ExtractedWorkflow(
            name="order_lifecycle",
            entity_name="order",
            states=["pending", "confirmed", "shipped", "delivered"],
            initial="pending",
            source_file="models.py",
        )
        assert wf.initial == "pending"
        assert len(wf.states) == 4

    def test_to_emitter_data(self) -> None:
        wf = ExtractedWorkflow(
            name="order_lifecycle",
            entity_name="order",
            states=["pending", "shipped", "delivered"],
            initial="pending",
            transitions=[
                {"from": "pending", "to": "shipped"},
                {"from": "shipped", "to": "delivered"},
            ],
            source_file="models.py",
        )
        data = wf.to_emitter_data()
        assert data["initial"] == "pending"
        assert len(data["states"]) == 3
        assert len(data["transitions"]) == 2


class TestAnalysisReport:

    def test_create_empty(self) -> None:
        report = AnalysisReport(domain="test")
        assert report.domain == "test"
        assert len(report.entities) == 0

    def test_summary(self) -> None:
        report = AnalysisReport(
            domain="shop",
            entities=[
                ExtractedEntity(name="Product", source_file="m.py", fields=[]),
                ExtractedEntity(name="Order", source_file="m.py", fields=[]),
            ],
            routes=[ExtractedRoute(path="/products", method="GET", entity_name="product", source_file="r.py")],
            workflows=[ExtractedWorkflow(name="order_lifecycle", entity_name="order", states=["new", "done"], initial="new", source_file="m.py")],
        )
        s = report.summary()
        assert "2 entities" in s
        assert "1 route" in s
        assert "1 workflow" in s

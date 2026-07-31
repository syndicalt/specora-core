"""Tests for extractor.models — data structures for codebase analysis."""

import re

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
    is_sensitive_name,
    safe_contract_name,
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
                ExtractedField(
                    name="title", type="string", required=True, description="Book title"
                ),
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
            transitions={"pending": ["shipped"], "shipped": ["delivered"]},
            source_file="models.py",
        )
        data = wf.to_emitter_data()
        assert data["initial"] == "pending"
        assert len(data["states"]) == 3
        # workflow.meta.yaml declares spec.transitions as a map of source state
        # to target states. Emitting a list of {from, to} pairs made every
        # extraction that found a state machine fail validation.
        assert data["transitions"] == {"pending": ["shipped"], "shipped": ["delivered"]}

    def test_terminal_state_is_marked(self) -> None:
        wf = ExtractedWorkflow(
            name="order_lifecycle",
            entity_name="order",
            states=["pending", "shipped"],
            initial="pending",
            source_file="models.py",
        )
        data = wf.to_emitter_data()
        assert data["states"]["shipped"]["terminal"] is True
        assert "terminal" not in data["states"]["pending"]

    def test_unknown_transition_targets_are_dropped(self) -> None:
        wf = ExtractedWorkflow(
            name="order_lifecycle",
            entity_name="order",
            states=["pending", "shipped"],
            initial="pending",
            transitions={"pending": ["shipped", "vaporized"], "elsewhere": ["pending"]},
            source_file="models.py",
        )
        data = wf.to_emitter_data()
        assert data["transitions"] == {"pending": ["shipped"]}

    def test_initial_outside_states_falls_back(self) -> None:
        wf = ExtractedWorkflow(
            name="order_lifecycle",
            entity_name="order",
            states=["pending", "shipped"],
            initial="nonexistent",
            source_file="models.py",
        )
        assert wf.to_emitter_data()["initial"] == "pending"


class TestSafeContractName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("TicketCreate", "ticket_create"),
            ("../../etc/passwd", "etc_passwd"),
            ("/absolute/path", "absolute_path"),
            ("has spaces", "has_spaces"),
            ("kebab-case", "kebab_case"),
            ("9lives", "extracted_9lives"),
            ("", "extracted"),
            ("..", "extracted"),
            ("__dunder__", "dunder"),
        ],
    )
    def test_always_matches_the_meta_schema_pattern(self, raw: str, expected: str) -> None:
        result = safe_contract_name(raw)
        assert result == expected
        assert re.fullmatch(r"[a-z][a-z0-9_]*", result)


class TestSensitiveNames:
    @pytest.mark.parametrize(
        "name",
        [
            "password",
            "password_hash",
            "hashed_password",
            "api_key",
            "secret",
            "refresh_token",
            "ssn",
            "card_number",
            "passwordHash",
        ],
    )
    def test_credentials_are_sensitive(self, name: str) -> None:
        assert is_sensitive_name(name)

    @pytest.mark.parametrize(
        "name", ["token_count", "password_expires_at", "api_key_id", "name", "email", "title"]
    )
    def test_metadata_about_credentials_is_not(self, name: str) -> None:
        assert not is_sensitive_name(name)

    def test_emitter_data_marks_a_credential_write_only(self) -> None:
        entity = ExtractedEntity(
            name="user",
            source_file="m.py",
            fields=[
                ExtractedField(name="password_hash", type="string"),
                ExtractedField(name="email", type="email"),
            ],
        )
        fields = entity.to_emitter_data()["fields"]
        assert fields["password_hash"]["sensitive"] is True
        assert "sensitive" not in fields["email"]

    def test_emitter_data_sanitizes_field_names(self) -> None:
        entity = ExtractedEntity(
            name="user",
            source_file="m.py",
            fields=[ExtractedField(name="customerId", type="uuid")],
        )
        assert list(entity.to_emitter_data()["fields"]) == ["customer_id"]


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
            routes=[
                ExtractedRoute(
                    path="/products", method="GET", entity_name="product", source_file="r.py"
                )
            ],
            workflows=[
                ExtractedWorkflow(
                    name="order_lifecycle",
                    entity_name="order",
                    states=["new", "done"],
                    initial="new",
                    source_file="m.py",
                )
            ],
        )
        s = report.summary()
        assert "2 entities" in s
        assert "1 route" in s
        assert "1 workflow" in s

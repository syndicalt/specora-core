"""Tests for the Factory CLI paths that turn model output into files.

These cover the two failure modes that put bad contracts into `domains/`:
a name from a tool call reaching a filesystem path, and a contract that fails
its meta-schema being written anyway.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory.cli import chat
from factory.cli.add import _entity_columns
from factory.cli.migrate import _extract_contracts
from factory.emitters.entity_emitter import emit_entity


@pytest.fixture()
def contracts_base(tmp_path: Path, monkeypatch) -> Path:
    base = tmp_path / "domains"
    (base / "shop" / "entities").mkdir(parents=True)
    monkeypatch.setattr(chat, "_contracts_base", base)
    return base


def _answer_yes(monkeypatch) -> None:
    monkeypatch.setattr(chat.console, "input", lambda *_a, **_k: "y")


class TestProposeEntity:
    @pytest.mark.parametrize(
        "name", ["../../../../tmp/pwn", "..", "a/b", "", "1abc", None, {"name": "x"}]
    )
    def test_unusable_name_writes_nothing(self, contracts_base: Path, name: object) -> None:
        result = chat._propose_entity({"name": name, "fields": {"a": {"type": "string"}}}, "shop")
        assert result.startswith("Rejected")
        assert list(contracts_base.rglob("*.contract.yaml")) == []

    def test_contract_failing_its_schema_writes_nothing(self, contracts_base: Path) -> None:
        # `maxLength` at field level is not in entity.meta.yaml, which sets
        # additionalProperties: false. This used to print a "validation
        # warning ... will be auto-healed" and write the file regardless.
        result = chat._propose_entity(
            {"name": "widget", "fields": {"a": {"type": "string", "maxLength": 5}}}, "shop"
        )
        assert result.startswith("Rejected, nothing was written")
        assert list(contracts_base.rglob("*.contract.yaml")) == []

    def test_accepted_proposal_writes_entity_route_and_page(
        self, contracts_base: Path, monkeypatch
    ) -> None:
        _answer_yes(monkeypatch)
        chat._propose_entity(
            {
                "name": "widget",
                "description": "A widget",
                "fields": {"label": {"type": "string", "required": True}},
            },
            "shop",
        )
        assert (contracts_base / "shop" / "entities" / "widget.contract.yaml").exists()
        assert (contracts_base / "shop" / "routes" / "widgets.contract.yaml").exists()
        assert (contracts_base / "shop" / "pages" / "widgets.contract.yaml").exists()

    def test_workflow_binding_reaches_the_route(self, contracts_base: Path, monkeypatch) -> None:
        _answer_yes(monkeypatch)
        chat._propose_entity(
            {
                "name": "widget",
                "fields": {"label": {"type": "string"}},
                "state_machine": "workflow/shop/widget_lifecycle",
            },
            "shop",
        )
        route = yaml.safe_load(
            (contracts_base / "shop" / "routes" / "widgets.contract.yaml").read_text()
        )
        assert "workflow/shop/widget_lifecycle" in route["requires"]
        assert any(ep["path"] == "/{id}/state" for ep in route["spec"]["endpoints"])

    def test_existing_contract_is_never_clobbered(self, contracts_base: Path, monkeypatch) -> None:
        target = contracts_base / "shop" / "entities" / "widget.contract.yaml"
        target.write_text("original", encoding="utf-8")
        _answer_yes(monkeypatch)

        result = chat._propose_entity(
            {"name": "widget", "fields": {"label": {"type": "string"}}}, "shop"
        )
        assert result.startswith("Rejected")
        assert target.read_text(encoding="utf-8") == "original"

    def test_declined_proposal_writes_nothing(self, contracts_base: Path, monkeypatch) -> None:
        monkeypatch.setattr(chat.console, "input", lambda *_a, **_k: "n")
        chat._propose_entity({"name": "widget", "fields": {"a": {"type": "string"}}}, "shop")
        assert list(contracts_base.rglob("*.contract.yaml")) == []


class TestProposeModification:
    @pytest.mark.parametrize("fqn", ["entity/../secrets", "entity/shop/../../../etc/x"])
    def test_traversal_is_rejected(self, contracts_base: Path, fqn: str) -> None:
        result = chat._propose_modification({"contract_fqn": fqn, "instruction": "x"}, "shop")
        assert result.startswith(("Rejected", "Invalid FQN"))

    def test_other_domain_is_rejected(self, contracts_base: Path) -> None:
        result = chat._propose_modification(
            {"contract_fqn": "entity/other/thing", "instruction": "x"}, "shop"
        )
        assert "not in domain 'shop'" in result


class TestExecuteTool:
    def test_missing_argument_returns_a_result_instead_of_raising(
        self, contracts_base: Path
    ) -> None:
        # A tool call without a result desynchronises the conversation, so
        # every failure has to come back as a string.
        assert chat._execute_tool("propose_modification", {}, "shop").startswith("Rejected")

    def test_non_dict_arguments_are_rejected(self, contracts_base: Path) -> None:
        assert chat._execute_tool("propose_entity", "oops", "shop").startswith("Rejected")

    def test_unknown_tool_is_reported(self, contracts_base: Path) -> None:
        assert chat._execute_tool("drop_tables", {}, "shop").startswith("Unknown tool")


class TestAddPageColumns:
    def test_columns_come_from_the_entity_contract(self, tmp_path: Path) -> None:
        entities = tmp_path / "entities"
        entities.mkdir()
        (entities / "widget.contract.yaml").write_text(
            emit_entity(
                "widget",
                "shop",
                {
                    "fields": {
                        "title": {"type": "string"},
                        "api_key": {"type": "string", "sensitive": True},
                    }
                },
            ),
            encoding="utf-8",
        )
        # `title`, not the old hard-coded `["name"]`, and the secret is dropped.
        assert _entity_columns(tmp_path, "entity/shop/widget") == ["title"]

    def test_missing_entity_yields_no_columns(self, tmp_path: Path) -> None:
        # An empty list lets the frontend generator infer; a guess would be a
        # GenerationError.
        assert _entity_columns(tmp_path, "entity/shop/absent") == []

    def test_unparseable_entity_yields_no_columns(self, tmp_path: Path) -> None:
        entities = tmp_path / "entities"
        entities.mkdir()
        (entities / "widget.contract.yaml").write_text("{[not yaml", encoding="utf-8")
        assert _entity_columns(tmp_path, "entity/shop/widget") == []


class TestMigrateExtraction:
    def test_reads_fenced_multi_document_blocks(self) -> None:
        response = (
            "Here you go:\n```yaml\n"
            "apiVersion: specora.dev/v1\nkind: Entity\nmetadata: {name: a}\n"
            "\n---\n"
            "apiVersion: specora.dev/v1\nkind: Entity\nmetadata: {name: b}\n"
            "```\n"
        )
        assert [c["metadata"]["name"] for c in _extract_contracts(response)] == ["a", "b"]

    def test_reads_an_unfenced_response(self) -> None:
        # A model that answers without a fence used to produce "No contracts
        # could be extracted" with the payload right there.
        response = "apiVersion: specora.dev/v1\nkind: Entity\nmetadata:\n  name: a\n"
        assert [c["metadata"]["name"] for c in _extract_contracts(response)] == ["a"]

    def test_unparseable_document_does_not_take_the_good_ones_with_it(self) -> None:
        response = (
            "```yaml\n"
            "apiVersion: specora.dev/v1\nkind: Entity\nmetadata: {name: a}\n"
            "\n---\n"
            "\tthis: is not [valid\n"
            "```\n"
        )
        assert [c["metadata"]["name"] for c in _extract_contracts(response)] == ["a"]

    def test_document_without_apiversion_is_discarded(self) -> None:
        response = "```yaml\nkind: Entity\nmetadata: {name: a}\n```\n"
        assert _extract_contracts(response) == []

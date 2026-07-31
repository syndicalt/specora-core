"""Tests for extractor.analyzers.typescript_types — the brace-matching reader."""

from pathlib import Path

from extractor.analyzers.typescript_types import analyze_typescript_types


def _analyze(tmp_path: Path, source: str, name: str = "types.ts"):
    (tmp_path / name).write_text(source, encoding="utf-8")
    warnings: list[str] = []
    entities = analyze_typescript_types([name], tmp_path, warnings=warnings)
    return {e.name: e for e in entities}, warnings


def _fields(entity):
    return {f.name: f for f in entity.fields}


class TestInterfaces:
    def test_extracts_members_with_types_and_optionality(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
export interface Ticket {
  id: string;
  subject: string;
  priority?: number;
  resolved: boolean;
  createdAt: Date;
  tags: string[];
  meta: Record<string, unknown>;
  closedAt: string | null;
}
""",
        )
        fields = _fields(entities["Ticket"])
        assert fields["id"].type == "uuid"
        assert fields["subject"].type == "string"
        assert fields["subject"].required is True
        assert fields["priority"].required is False
        assert fields["resolved"].type == "boolean"
        assert fields["createdAt"].type == "datetime"
        assert fields["tags"].type == "array"
        assert fields["meta"].type == "object"
        assert fields["closedAt"].required is False

    def test_string_literal_union_becomes_an_enum(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "export interface Ticket {\n  status: 'open' | 'closed' | 'on_hold';\n}\n",
        )
        field = _fields(entities["Ticket"])["status"]
        assert field.enum_values == ["open", "closed", "on_hold"]
        assert entities["Ticket"].state_field == "status"

    def test_type_alias_object_literal(self, tmp_path: Path) -> None:
        entities, _ = _analyze(tmp_path, "export type Book = {\n  title: string;\n};\n")
        assert set(_fields(entities["Book"])) == {"title"}

    def test_non_object_type_alias_is_reported_not_invented(self, tmp_path: Path) -> None:
        entities, warnings = _analyze(
            tmp_path,
            "export type Id = string;\nexport interface Book { title: string }\n",
        )
        assert "Id" not in entities
        assert "Book" in entities

    def test_nested_object_literal_does_not_break_member_parsing(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
export interface Ticket {
  id: string;
  author: { name: string; email: string };
  subject: string;
}
""",
        )
        fields = _fields(entities["Ticket"])
        assert set(fields) == {"id", "author", "subject"}
        assert fields["author"].type == "object"

    def test_methods_and_index_signatures_are_not_fields(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
export interface Client {
  baseUrl: string;
  fetch(path: string): Promise<void>;
  [key: string]: unknown;
}
""",
        )
        assert set(_fields(entities["Client"])) == {"baseUrl"}

    def test_comments_do_not_produce_members(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
export interface Book {
  /* the title: not a field */
  title: string; // trailing: also not a field
}
""",
        )
        assert set(_fields(entities["Book"])) == {"title"}

    def test_reference_fields(self, tmp_path: Path) -> None:
        entities, _ = _analyze(tmp_path, "export interface Order {\n  customerId: string;\n}\n")
        field = _fields(entities["Order"])["customerId"]
        assert field.reference_entity == "customer"
        assert field.type == "uuid"

    def test_credentials_are_marked_sensitive(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path, "export interface User {\n  apiKey: string;\n  name: string;\n}\n"
        )
        fields = _fields(entities["User"])
        assert fields["apiKey"].sensitive is True
        assert fields["name"].sensitive is False


class TestNoise:
    def test_react_prop_interfaces_are_not_entities(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "export interface ButtonProps { label: string }\n"
            "export interface Ticket { subject: string }\n",
            name="components.ts",
        )
        assert set(entities) == {"Ticket"}

    def test_dto_suffixes_collapse(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "export interface TicketCreate { subject: string }\n"
            "export interface TicketResponse { id: string; subject: string }\n",
        )
        assert set(entities) == {"Ticket"}

    def test_cursor_page_envelope_is_not_an_entity(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "export interface CursorList { items: string[]; nextCursor: string | null }\n",
        )
        assert entities == {}


class TestHostileInput:
    def test_unbalanced_braces_are_reported(self, tmp_path: Path) -> None:
        entities, warnings = _analyze(tmp_path, "export interface Broken {\n  a: string;\n")
        assert entities == {}
        assert any("unbalanced braces" in w for w in warnings)

    def test_braces_inside_string_literals_do_not_confuse_matching(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "export interface Book {\n  kind: '{weird}';\n  title: string;\n}\n",
        )
        assert set(_fields(entities["Book"])) == {"kind", "title"}

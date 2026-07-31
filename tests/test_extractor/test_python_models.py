"""Tests for extractor.analyzers.python_models — the deterministic AST reader."""

from pathlib import Path

from extractor.analyzers.python_models import analyze_python_models
from extractor.models import Confidence


def _analyze(tmp_path: Path, source: str, name: str = "models.py"):
    (tmp_path / name).write_text(source, encoding="utf-8")
    warnings: list[str] = []
    entities = analyze_python_models([name], tmp_path, warnings=warnings)
    return {e.name: e for e in entities}, warnings


def _fields(entity):
    return {f.name: f for f in entity.fields}


class TestPydantic:
    def test_extracts_fields_with_types_and_requiredness(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


class Invoice(BaseModel):
    "A customer invoice."
    id: UUID
    email: EmailStr
    total: Decimal = Field(..., description="Amount owed", gt=0)
    memo: Optional[str] = None
    paid: bool = False
    issued_at: datetime
    line_items: list[str] = []
""",
        )
        invoice = entities["Invoice"]
        assert invoice.description == "A customer invoice."
        assert invoice.confidence == Confidence.HIGH

        fields = _fields(invoice)
        assert fields["id"].type == "uuid"
        assert fields["email"].type == "email"
        # decimal, not number: conflating them loses money in the ledger domain.
        assert fields["total"].type == "decimal"
        assert fields["total"].required is True
        assert fields["total"].description == "Amount owed"
        assert fields["total"].constraints == {"min": 0}
        assert fields["memo"].type == "string"
        assert fields["memo"].required is False
        assert fields["paid"].type == "boolean"
        assert fields["paid"].required is False
        assert fields["issued_at"].type == "datetime"
        assert fields["issued_at"].required is True
        assert fields["line_items"].type == "array"

    def test_pep604_optional_is_not_required(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "from pydantic import BaseModel\n\n"
            "class A(BaseModel):\n    x: str | None\n    y: str\n",
        )
        fields = _fields(entities["A"])
        assert fields["x"].required is False
        assert fields["y"].required is True

    def test_literal_and_enum_become_contract_enums(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
from enum import Enum
from typing import Literal
from pydantic import BaseModel


class Status(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class Ticket(BaseModel):
    status: Status
    tier: Literal["gold", "silver"]
""",
        )
        fields = _fields(entities["Ticket"])
        assert fields["status"].enum_values == ["open", "closed"]
        assert fields["tier"].enum_values == ["gold", "silver"]
        assert entities["Ticket"].state_field == "status"
        assert entities["Ticket"].state_values == ["open", "closed"]

    def test_enum_class_is_not_itself_an_entity(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            'from enum import Enum\n\nclass Status(str, Enum):\n    OPEN = "open"\n',
        )
        assert "Status" not in entities

    def test_credentials_are_marked_sensitive(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "from pydantic import BaseModel\n\n"
            "class User(BaseModel):\n"
            "    password_hash: str\n"
            "    login_count: int\n",
        )
        fields = _fields(entities["User"])
        assert fields["password_hash"].sensitive is True
        assert fields["login_count"].sensitive is False

    def test_id_suffix_becomes_a_reference(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "from pydantic import BaseModel\n\n"
            "class Order(BaseModel):\n    customer_id: str\n    total: float\n",
        )
        field = _fields(entities["Order"])["customer_id"]
        assert field.reference_entity == "customer"
        assert field.reference_edge == "CUSTOMER"
        assert field.type == "uuid"

    def test_max_length_becomes_a_constraint(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            "from pydantic import BaseModel, Field\n\n"
            "class A(BaseModel):\n"
            "    subject: str = Field(..., max_length=300)\n"
            "    body: str\n",
        )
        assert _fields(entities["A"])["subject"].constraints == {"maxLength": 300}


class TestSQLAlchemy:
    def test_extracts_columns_nullability_and_foreign_keys(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
from sqlalchemy import Column, ForeignKey, Integer, Numeric, String, Text, Boolean
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    reference = Column(String(64), nullable=False)
    notes = Column(Text)
    amount = Column(Numeric(12, 4), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    archived = Column(Boolean, default=False)
""",
        )
        fields = _fields(entities["Order"])
        assert fields["id"].required is True
        assert fields["id"].immutable is True
        assert fields["reference"].type == "string"
        assert fields["reference"].constraints == {"maxLength": 64}
        assert fields["reference"].required is True
        assert fields["notes"].type == "text"
        assert fields["notes"].required is False
        assert fields["amount"].type == "decimal"
        assert fields["amount"].constraints == {"precision": 12, "scale": 4}
        assert fields["customer_id"].reference_entity == "customer"
        assert fields["archived"].required is False

    def test_mapped_column_style(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Widget(Base):
    __tablename__ = "widgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)
""",
        )
        fields = _fields(entities["Widget"])
        assert fields["id"].type == "integer"
        assert fields["label"].type == "string"
        assert fields["label"].required is True


class TestDataclasses:
    def test_dataclass_and_typeddict(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
from dataclasses import dataclass
from typing import ClassVar, TypedDict


@dataclass
class Point:
    x: int
    y: int
    kind: ClassVar[str] = "point"


class Config(TypedDict):
    host: str
""",
        )
        assert set(_fields(entities["Point"])) == {"x", "y"}
        # Config ends in a plumbing suffix, so it is reported as skipped.
        assert "Config" not in entities


class TestNoise:
    def test_dto_projections_collapse_onto_one_entity_name(self, tmp_path: Path) -> None:
        entities, _ = _analyze(
            tmp_path,
            """
from pydantic import BaseModel


class TicketCreate(BaseModel):
    subject: str


class TicketUpdate(BaseModel):
    subject: str | None


class TicketResponse(BaseModel):
    id: str
    subject: str
""",
        )
        assert set(entities) == {"Ticket"}

    def test_single_field_request_bodies_are_not_entities(self, tmp_path: Path) -> None:
        entities, warnings = _analyze(
            tmp_path,
            "from pydantic import BaseModel\n\n"
            "class TicketStateChange(BaseModel):\n    state: str\n",
        )
        assert entities == {}
        assert any("single field" in w for w in warnings)

    def test_result_pages_are_not_entities(self, tmp_path: Path) -> None:
        entities, warnings = _analyze(
            tmp_path,
            """
from pydantic import BaseModel


class TicketPage(BaseModel):
    items: list[str]
    next_cursor: str | None
""",
        )
        assert entities == {}
        assert any("result page" in w for w in warnings)


class TestHostileInput:
    def test_unparseable_file_is_reported_not_swallowed(self, tmp_path: Path) -> None:
        entities, warnings = _analyze(tmp_path, "class Broken(BaseModel\n  ### not python\n")
        assert entities == {}
        assert any("not parseable as Python" in w for w in warnings)

    def test_analysis_never_executes_the_scanned_code(self, tmp_path: Path) -> None:
        marker = tmp_path / "executed"
        source = (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('boom')\n"
            "from pydantic import BaseModel\n\n"
            "class A(BaseModel):\n    x: str\n    y: int\n"
        )
        entities, _ = _analyze(tmp_path, source)
        assert "A" in entities
        assert not marker.exists()

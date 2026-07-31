"""End-to-end tests for the 4-pass pipeline.

These assert the property the Extractor exists for: point it at a codebase and
the contracts it writes describe that codebase and compile.
"""

from pathlib import Path

import pytest

from extractor.emitter import emit_contracts
from extractor.synthesizer import synthesize
from forge.ir.compiler import Compiler
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all

BACKEND_MODELS = '''
"""Domain models."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class OrderStatus(str, Enum):
    PENDING = "pending"
    SHIPPED = "shipped"
    DELIVERED = "delivered"


class Customer(BaseModel):
    """Someone who buys things."""

    id: UUID
    name: str = Field(..., max_length=200)
    email: EmailStr
    password_hash: str


class CustomerCreate(BaseModel):
    name: str
    email: EmailStr
    password_hash: str


class Order(BaseModel):
    """A placed order."""

    id: UUID
    customer_id: UUID
    total: Decimal
    status: OrderStatus
    placed_at: datetime
    note: str | None = None


class OrderPage(BaseModel):
    items: list[Order]
    next_cursor: str | None
'''

BACKEND_ROUTES = """
from fastapi import APIRouter

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/", summary="List orders")
async def list_orders():
    ...


@router.post("/", summary="Create an order")
async def create_order():
    ...


@router.delete("/{record_id}")
async def delete_order(record_id: str):
    ...
"""

FRONTEND_TYPES = """
export interface Customer {
  id: string;
  name: string;
  email: string;
}

export interface ButtonProps {
  label: string;
}
"""


@pytest.fixture
def sample_app(tmp_path: Path) -> Path:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "models.py").write_text(BACKEND_MODELS, encoding="utf-8")
    (tmp_path / "backend" / "routes_orders.py").write_text(BACKEND_ROUTES, encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "types.ts").write_text(FRONTEND_TYPES, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_orders.py").write_text("def test_x(): pass\n", encoding="utf-8")
    return tmp_path


class TestSynthesize:
    def test_finds_the_entities_and_nothing_else(self, sample_app: Path) -> None:
        report = synthesize(sample_app, "shop")
        assert {e.name for e in report.entities} == {"customer", "order"}

    def test_fields_survive_the_pipeline(self, sample_app: Path) -> None:
        report = synthesize(sample_app, "shop")
        order = next(e for e in report.entities if e.name == "order")
        by_name = {f.name: f for f in order.fields}
        assert by_name["total"].type == "decimal"
        assert by_name["customer_id"].reference_entity == "entity/shop/customer"
        assert by_name["note"].required is False
        assert order.state_field == "status"
        assert order.state_values == ["pending", "shipped", "delivered"]

    def test_credentials_are_carried_through_as_sensitive(self, sample_app: Path) -> None:
        report = synthesize(sample_app, "shop")
        customer = next(e for e in report.entities if e.name == "customer")
        assert next(f for f in customer.fields if f.name == "password_hash").sensitive is True

    def test_workflow_is_detected_from_the_status_enum(self, sample_app: Path) -> None:
        report = synthesize(sample_app, "shop")
        assert [w.name for w in report.workflows] == ["order_lifecycle"]

    def test_routes_are_found_with_their_prefix(self, sample_app: Path) -> None:
        report = synthesize(sample_app, "shop")
        assert {(r.method, r.path) for r in report.routes} == {
            ("GET", "/orders"),
            ("POST", "/orders"),
            ("DELETE", "/orders/{record_id}"),
        }
        assert {r.entity_name for r in report.routes} == {"order"}

    def test_test_files_are_not_analyzed(self, sample_app: Path) -> None:
        report = synthesize(sample_app, "shop")
        assert all("tests/" not in e.source_file for e in report.entities)

    def test_emitted_contracts_validate_and_compile(self, sample_app: Path, tmp_path: Path) -> None:
        report = synthesize(sample_app, "shop")
        out = tmp_path / "out" / "shop"
        emit_contracts(report, out)

        errors = [e for e in validate_all(load_all_contracts(out)) if e.severity == "error"]
        assert [f"{e.contract_fqn} {e.path}: {e.message}" for e in errors] == []

        ir = Compiler(contract_root=out).compile()
        assert {e.name for e in ir.entities} == {"customer", "order"}

    def test_a_broken_file_does_not_silently_shrink_the_report(self, sample_app: Path) -> None:
        (sample_app / "backend" / "broken_models.py").write_text(
            "class Nope(BaseModel\n", encoding="utf-8"
        )
        report = synthesize(sample_app, "shop")
        assert {e.name for e in report.entities} == {"customer", "order"}
        assert any("broken_models.py" in w for w in report.warnings)
        assert "warnings" in report.summary()

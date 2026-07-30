"""Tests for the TypeScript interface generator."""
from forge.ir.model import DomainIR, EntityIR, FieldIR, ReferenceIR
from forge.targets.typescript.gen_types import TypeScriptGenerator


def _types(ir: DomainIR) -> str:
    return TypeScriptGenerator().generate(ir)[0].content


def test_wire_types_match_the_canonical_map() -> None:
    ir = DomainIR(domain="shop", entities=[EntityIR(
        fqn="entity/shop/order", name="order", domain="shop", table_name="orders",
        fields=[
            # decimal is a string on the wire: a JSON number is a float, which
            # would undo the precision the NUMERIC column exists to keep.
            FieldIR(name="amount", type="decimal", required=True),
            FieldIR(name="ratio", type="number", required=True),
            FieldIR(name="created_at", type="datetime", required=True),
            FieldIR(name="id", type="uuid", required=True),
            FieldIR(name="tags", type="array", items_type="string"),
        ],
    )])
    ts = _types(ir)
    assert "  amount: string;" in ts
    assert "  ratio: number;" in ts
    assert "  created_at: string;" in ts
    assert "  id: string;" in ts
    assert "  tags?: Array<string>;" in ts


def test_multi_domain_interfaces_do_not_collide() -> None:
    ir = DomainIR(
        domain="billing",
        domains=["billing", "support"],
        entities=[
            EntityIR(fqn="entity/billing/account", name="account", domain="billing",
                     table_name="billing_accounts",
                     fields=[FieldIR(name="id", type="uuid", required=True)]),
            EntityIR(fqn="entity/support/account", name="account", domain="support",
                     table_name="support_accounts",
                     fields=[FieldIR(name="id", type="uuid", required=True)]),
        ],
    )
    ts = _types(ir)
    assert "export interface BillingAccount {" in ts
    assert "export interface SupportAccount {" in ts


def test_reference_jsdoc_points_at_the_targets_own_interface() -> None:
    ir = DomainIR(
        domain="billing",
        domains=["billing", "support"],
        entities=[
            EntityIR(fqn="entity/support/agent", name="agent", domain="support",
                     table_name="support_agents",
                     fields=[FieldIR(name="id", type="uuid", required=True)]),
            EntityIR(fqn="entity/billing/invoice", name="invoice", domain="billing",
                     table_name="billing_invoices",
                     fields=[FieldIR(name="agent_id", type="uuid", reference=ReferenceIR(
                         target_entity="entity/support/agent"))]),
        ],
    )
    assert "@see SupportAgent" in _types(ir)


def test_multiline_description_stays_inside_its_jsdoc() -> None:
    ir = DomainIR(domain="shop", entities=[EntityIR(
        fqn="entity/shop/order", name="order", domain="shop", table_name="orders",
        description="First line.\nSecond line.",
        fields=[FieldIR(name="id", type="uuid", required=True)],
    )])
    assert "/** First line. Second line. */" in _types(ir)

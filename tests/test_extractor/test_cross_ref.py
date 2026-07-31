"""Tests for extractor.cross_ref — relationship and workflow resolution."""

from extractor.cross_ref import cross_reference
from extractor.models import ExtractedEntity, ExtractedField, ExtractedRoute


def _entity(name: str, **kwargs) -> ExtractedEntity:
    kwargs.setdefault("source_file", "m.py")
    kwargs.setdefault("fields", [])
    return ExtractedEntity(name=name, **kwargs)


class TestReferences:
    def test_resolved_reference_becomes_an_fqn(self) -> None:
        entities = [
            _entity("customer", fields=[ExtractedField(name="name", type="string")]),
            _entity(
                "order",
                fields=[ExtractedField(name="customer_id", reference_entity="customer")],
            ),
        ]
        entities, _, _ = cross_reference(entities, [], "shop")
        field = entities[1].fields[0]
        assert field.reference_entity == "entity/shop/customer"
        assert field.reference_edge == "CUSTOMER"
        assert field.reference_display == "name"

    def test_reference_type_is_coerced_to_uuid(self) -> None:
        """The target's primary key comes from `mixin/stdlib/identifiable`.

        A legacy `Column(Integer, ForeignKey(...))` otherwise emits an INTEGER
        column pointing at a UUID key, and the Postgres generator rejects it.
        """
        entities = [
            _entity("customer", fields=[ExtractedField(name="name", type="string")]),
            _entity(
                "order",
                fields=[
                    ExtractedField(
                        name="customer_id",
                        type="integer",
                        reference_entity="customer",
                        constraints={"maxLength": 64},
                    )
                ],
            ),
        ]
        entities, _, _ = cross_reference(entities, [], "shop")
        field = entities[1].fields[0]
        assert field.type == "uuid"
        assert "maxLength" not in field.constraints

    def test_qualified_reference_resolves_to_the_bare_entity(self) -> None:
        entities = [
            _entity("agent", fields=[ExtractedField(name="name", type="string")]),
            _entity(
                "ticket",
                fields=[
                    ExtractedField(
                        name="assigned_agent_id", reference_entity="assigned_agent"
                    )
                ],
            ),
        ]
        entities, _, _ = cross_reference(entities, [], "helpdesk")
        assert entities[1].fields[0].reference_entity == "entity/helpdesk/agent"

    def test_unresolvable_reference_is_demoted_and_reported(self) -> None:
        entities = [_entity("order", fields=[ExtractedField(name="x_id", reference_entity="x")])]
        warnings: list[str] = []
        entities, _, _ = cross_reference(entities, [], "shop", warnings=warnings)
        assert entities[0].fields[0].reference_entity == ""
        assert any("was not extracted" in w for w in warnings)

    def test_self_reference_is_dropped_and_reported(self) -> None:
        entities = [
            _entity(
                "account",
                fields=[
                    ExtractedField(name="name", type="string"),
                    ExtractedField(name="parent_account_id", reference_entity="parent_account"),
                ],
            )
        ]
        warnings: list[str] = []
        entities, _, _ = cross_reference(entities, [], "ledger", warnings=warnings)
        assert entities[0].fields[1].reference_entity == ""
        assert any("self-reference" in w for w in warnings)

    def test_display_field_is_chosen_from_the_targets_real_fields(self) -> None:
        entities = [
            _entity("offer", fields=[ExtractedField(name="number", type="string")]),
            _entity(
                "transaction", fields=[ExtractedField(name="offer_id", reference_entity="offer")]
            ),
        ]
        entities, _, _ = cross_reference(entities, [], "market")
        assert entities[1].fields[0].reference_display == "number"


class TestWorkflows:
    def test_one_workflow_per_entity(self) -> None:
        entities = [
            _entity(
                "order",
                fields=[ExtractedField(name="status", type="string")],
                state_field="status",
                state_values=["Pending", "Shipped"],
            )
        ]
        _, _, workflows = cross_reference(entities, [], "shop")
        assert len(workflows) == 1
        assert workflows[0].states == ["pending", "shipped"]

    def test_a_single_state_is_not_a_workflow(self) -> None:
        entities = [_entity("order", state_field="status", state_values=["pending"])]
        _, _, workflows = cross_reference(entities, [], "shop")
        assert workflows == []


class TestRoutes:
    def test_route_entity_names_are_normalized(self) -> None:
        routes = [
            ExtractedRoute(
                path="/x", method="GET", entity_name="MyThing", source_file="r.py"
            )
        ]
        _, routes, _ = cross_reference([], routes, "shop")
        assert routes[0].entity_name == "my_thing"

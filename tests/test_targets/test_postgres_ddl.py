"""Tests for the PostgreSQL DDL generator.

Each test here pins a defect that shipped: a contract that validated with zero
errors produced DDL PostgreSQL refuses to execute.
"""
import pytest

from forge.ir.model import DomainIR, EntityIR, FieldIR, PageIR, ReferenceIR
from forge.targets.base import GenerationError
from forge.targets.postgres.gen_ddl import PostgresGenerator, SchemaContext, foreign_key_statements


def _entity(name="order", table="orders", fields=None, fqn=None, domain="shop"):
    return EntityIR(
        fqn=fqn or f"entity/{domain}/{name}",
        name=name,
        domain=domain,
        table_name=table,
        fields=fields or [],
    )


def _identity_fields():
    return [
        FieldIR(name="id", type="uuid", computed="uuid", required=True),
        FieldIR(name="number", type="string"),
        FieldIR(name="created_at", type="datetime", computed="now", required=True),
        FieldIR(name="updated_at", type="datetime", computed="now_on_update", required=True),
    ]


def _schema(ir: DomainIR) -> str:
    return PostgresGenerator().generate(ir)[0].content


class TestIdentifierQuoting:

    def test_reserved_word_columns_are_quoted(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=[
            FieldIR(name="order", type="string", required=True),
            FieldIR(name="group", type="string"),
            FieldIR(name="limit", type="integer"),
            *_identity_fields(),
        ])])
        sql = _schema(ir)
        assert '"order" TEXT NOT NULL' in sql
        assert '"group" TEXT' in sql
        assert '"limit" INTEGER' in sql

    def test_table_name_is_quoted(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        assert 'CREATE TABLE "orders" (' in _schema(ir)

    def test_over_long_identifier_is_rejected(self) -> None:
        long_name = "x" * 64
        ir = DomainIR(domain="shop", entities=[_entity(fields=[
            FieldIR(name=long_name, type="string"), *_identity_fields(),
        ])])
        with pytest.raises(ValueError, match="exceeding the"):
            _schema(ir)


class TestDefaults:

    def test_apostrophe_in_default_is_escaped(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=[
            FieldIR(name="note", type="string", default="O'Brien"), *_identity_fields(),
        ])])
        assert """DEFAULT 'O''Brien'""" in _schema(ir)

    def test_computed_now_gets_a_default(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        sql = _schema(ir)
        assert '"created_at" TIMESTAMPTZ NOT NULL DEFAULT now()' in sql
        assert '"updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()' in sql

    def test_server_computed_column_is_not_null_without_required(self) -> None:
        # A null created_at is invisible after page one under the generated
        # keyset predicate, so the DEFAULT must be paired with NOT NULL whether
        # or not the contract remembered `required: true`.
        ir = DomainIR(domain="shop", entities=[_entity(fields=[
            FieldIR(name="seen_at", type="datetime", computed="now"),
            *_identity_fields(),
        ])])
        assert '"seen_at" TIMESTAMPTZ NOT NULL DEFAULT now()' in _schema(ir)

    def test_app_computed_column_stays_nullable(self) -> None:
        # `current_user` is computed by the application, so nothing in the
        # database can guarantee a value and NOT NULL would be a lie.
        ir = DomainIR(domain="shop", entities=[_entity(fields=[
            FieldIR(name="created_by", type="string", computed="current_user"),
            *_identity_fields(),
        ])])
        sql = _schema(ir)
        assert '"created_by" TEXT' in sql
        assert '"created_by" TEXT NOT NULL' not in sql

    def test_now_on_update_gets_a_trigger(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        sql = _schema(ir)
        assert 'CREATE OR REPLACE FUNCTION "specora_set_updated_at"()' in sql
        assert 'CREATE TRIGGER "trg_orders_updated_at" BEFORE UPDATE ON "orders"' in sql


class TestNoExtensibilityColumn:

    def test_data_field_is_not_duplicated(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=[
            FieldIR(name="data", type="object"), *_identity_fields(),
        ])])
        sql = _schema(ir)
        assert sql.count('"data"') == 1
        assert "'{}'::jsonb" not in sql

    def test_pgcrypto_extension_is_not_required(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        assert "pgcrypto" not in _schema(ir)


class TestForeignKeys:

    def _two_entities(self, ref_type="uuid"):
        return DomainIR(domain="shop", entities=[
            _entity("customer", "customers", _identity_fields()),
            _entity("order", "orders", [
                FieldIR(name="customer_id", type=ref_type, required=True,
                        reference=ReferenceIR(target_entity="entity/shop/customer")),
                *_identity_fields(),
            ]),
        ])

    def test_reference_emits_a_foreign_key(self) -> None:
        sql = _schema(self._two_entities())
        assert (
            'ALTER TABLE "orders" ADD CONSTRAINT "fk_orders_customer_id" '
            'FOREIGN KEY ("customer_id") REFERENCES "customers" ("id") ON DELETE RESTRICT;'
        ) in sql

    def test_foreign_keys_follow_every_create_table(self) -> None:
        sql = _schema(self._two_entities())
        assert sql.index("ADD CONSTRAINT") > sql.rindex("CREATE TABLE ")

    def test_incomparable_reference_type_fails_at_generation(self) -> None:
        with pytest.raises(GenerationError, match="cannot build a foreign key"):
            _schema(self._two_entities(ref_type="string"))

    def test_unresolved_target_is_reported_not_dropped_silently(self) -> None:
        entity = _entity("order", "orders", [
            FieldIR(name="owner_id", type="uuid",
                    reference=ReferenceIR(target_entity="entity/other/owner")),
        ])
        out = foreign_key_statements(entity, SchemaContext.empty(), replace=False)
        assert out == [
            "-- No FOREIGN KEY for orders.owner_id: reference target entity/other/owner "
            "is not part of this build, so its table and primary key are unknown here."
        ]


class TestIndexes:

    def test_keyset_index_matches_the_generated_query(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        assert (
            'CREATE INDEX "idx_orders_keyset" ON "orders" ("created_at" DESC, "id" DESC);'
        ) in _schema(ir)

    def test_updated_at_is_not_indexed(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        assert "idx_orders_updated_at" not in _schema(ir)

    def test_number_is_not_indexed_twice(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        sql = _schema(ir)
        assert '"number" TEXT UNIQUE' in sql
        assert "idx_orders_number" not in sql

    def test_declared_filter_gets_a_composite_index(self) -> None:
        ir = DomainIR(
            domain="shop",
            entities=[_entity(fields=[FieldIR(name="state", type="string"), *_identity_fields()])],
            pages=[PageIR(fqn="page/shop/orders", name="orders", domain="shop", route="/orders",
                          entity_fqn="entity/shop/order",
                          views=[{"type": "table", "filterable": ["state"]}])],
        )
        assert (
            'CREATE INDEX "idx_orders_state" ON "orders" '
            '("state", "created_at" DESC, "id" DESC);'
        ) in _schema(ir)

    def test_undeclared_field_is_not_indexed(self) -> None:
        ir = DomainIR(domain="shop", entities=[
            _entity(fields=[FieldIR(name="priority", type="string"), *_identity_fields()]),
        ])
        assert "idx_orders_priority" not in _schema(ir)

    def test_named_quick_filters_are_not_mistaken_for_columns(self) -> None:
        ir = DomainIR(
            domain="shop",
            entities=[_entity(fields=_identity_fields())],
            pages=[PageIR(fqn="page/shop/orders", name="orders", domain="shop", route="/orders",
                          entity_fqn="entity/shop/order",
                          filters={"quick": ["mine", "recent"]})],
        )
        sql = _schema(ir)
        assert "idx_orders_mine" not in sql
        assert "idx_orders_recent" not in sql


class TestBootstrapOnly:

    def test_tables_are_unguarded_so_reruns_fail_loudly(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=_identity_fields())])
        sql = _schema(ir)
        assert "CREATE TABLE IF NOT EXISTS" not in sql
        assert "BOOTSTRAP ONLY" in sql

    def test_entity_without_fields_fails_at_generation(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=[])])
        with pytest.raises(GenerationError, match="declares no fields"):
            _schema(ir)


class TestTypeMapping:

    def test_decimal_is_parameterised_and_number_is_float(self) -> None:
        ir = DomainIR(domain="shop", entities=[_entity(fields=[
            FieldIR(name="amount", type="decimal",
                    constraints={"precision": 12, "scale": 4}),
            FieldIR(name="ratio", type="number"),
            *_identity_fields(),
        ])])
        sql = _schema(ir)
        assert '"amount" NUMERIC(12, 4)' in sql
        assert '"ratio" DOUBLE PRECISION' in sql

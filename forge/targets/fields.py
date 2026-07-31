"""Which fields a client may write, and which the server may disclose.

Three generators need this partition — the Pydantic models, the generated test
suite, and the React form components — and each had reinvented or reached
across for it. `gen_tests` imported `gen_models._writable_fields` through the
underscore, and `gen_components` kept a fourth private copy. A partition that
decides what a client can set and what the API hands back must not be defined
in three places with three sets of edge cases.

The distinction that matters, and that was previously wrong:

    immutable  =  cannot be changed AFTER creation

so an immutable field is *settable at creation* and *rejected on update*. Folding
both into one "writable" set excluded immutable fields from the Create model as
well, which for an entity whose fields are all immutable produced a Create model
that accepted nothing at all. `entity/financial_ledger/audit_event` is exactly
that shape — an append-only audit record, where immutable-on-every-field is the
correct modelling — and every create against it stored a row holding only `id`
and the timestamps, then failed response validation on the five fields the
client had never been permitted to supply.
"""

from __future__ import annotations

from forge.ir.model import EntityIR, FieldIR

STATE_FIELD = "state"


def is_lifecycle_managed(entity: EntityIR, field: FieldIR) -> bool:
    """Whether a field is owned by the workflow rather than by clients.

    The `state` column of a workflow-bound entity moves only through the
    transition endpoint, where the declared transitions and guards are enforced.
    Exposing it on Create or Update would let a client set any state directly
    and bypass the state machine entirely — which is what it used to do.
    """
    return entity.state_machine is not None and field.name == STATE_FIELD


def creatable_fields(entity: EntityIR) -> list[FieldIR]:
    """Fields a client may supply when creating a record.

    Excludes only server-computed values and workflow-managed state. Immutable
    and sensitive fields are both included: immutable means unchangeable after
    creation, and write-only means write-*able* — a password hash that could not
    be set would leave the account with no credential.
    """
    return [f for f in entity.fields if not f.computed and not is_lifecycle_managed(entity, f)]


def updatable_fields(entity: EntityIR) -> list[FieldIR]:
    """Fields a client may change on an existing record.

    This is where `immutable` applies, and where `id` is dropped — moving a
    primary key orphans the row rather than renaming it.
    """
    return [f for f in creatable_fields(entity) if not f.immutable and f.name != "id"]


def disclosable_fields(entity: EntityIR) -> list[FieldIR]:
    """Fields the server is willing to serialise back to a client.

    Sensitive fields are omitted outright rather than defaulted to None or
    marked `exclude=True`. An excluded-but-declared field is one
    `response_model_exclude` override, one `.model_dump()` in a future handler,
    or one `by_alias` flag away from being emitted again; a field the class does
    not have cannot be serialised by any of them.
    """
    return [f for f in entity.fields if not f.sensitive]

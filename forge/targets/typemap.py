"""Canonical IR-type to target-type mappings.

This is the single source of truth. It previously existed as a `PYTHON_TYPE_MAP`
dict copy-pasted byte-identically into `gen_models`, `gen_routes`, and
`gen_repositories` (one copy dead), plus a separate `PG_TYPE_MAP` in `gen_ddl`
that silently disagreed with them.

The disagreements were not cosmetic. Two mattered:

  * `datetime`/`uuid` mapped to Python `str` while PostgreSQL mapped them to
    TIMESTAMPTZ/UUID. asyncpg returns `datetime`/`UUID` objects, so the Pydantic
    response models were wrong about their own payloads. Nothing broke only
    because no route declared `response_model=`; wiring response models up
    without fixing the map would have turned every GET into a 500.

  * `number` mapped to Python `float` but PostgreSQL `NUMERIC`. Amounts lost
    precision in the Pydantic layer *before* reaching an exact-precision
    column — silent rounding on a domain that ships a financial ledger.

`number` and `decimal` are deliberately distinct. The shipped domains use
`number` for genuinely inexact quantities (orbital altitude, latitude, star
ratings) and for money. Those need different representations, so overloading
one type cannot be correct for both:

    number   -> float / DOUBLE PRECISION   (inexact, fast, JSON-native)
    decimal  -> Decimal / NUMERIC(p, s)    (exact, for money and accounting)

`decimal` serialises to a JSON *string* rather than a number, because a JSON
number is a float and would reintroduce the precision loss this type exists to
prevent.
"""

from __future__ import annotations

# IR type -> Python / Pydantic annotation.
PY_TYPES: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "decimal": "Decimal",
    "boolean": "bool",
    "text": "str",
    "array": "list",
    "object": "dict",
    "datetime": "datetime",
    "date": "date",
    "uuid": "UUID",
    "email": "EmailStr",
}

# Imports each Python annotation requires, so generators emit exactly the
# imports they use instead of a fixed preamble (which is where 63 of the
# repo's unused-import warnings came from).
PY_TYPE_IMPORTS: dict[str, str] = {
    "Decimal": "from decimal import Decimal",
    "datetime": "from datetime import datetime",
    "date": "from datetime import date",
    "UUID": "from uuid import UUID",
    "EmailStr": "from pydantic import EmailStr",
}

# IR type -> PostgreSQL column type. `decimal` is parameterised at call time
# from the field's precision/scale constraints, so it is resolved by
# `pg_column_type` rather than looked up directly here.
PG_TYPES: dict[str, str] = {
    "string": "TEXT",
    "integer": "INTEGER",
    "number": "DOUBLE PRECISION",
    "decimal": "NUMERIC",
    "boolean": "BOOLEAN",
    "text": "TEXT",
    "array": "JSONB",
    "object": "JSONB",
    "datetime": "TIMESTAMPTZ",
    "date": "DATE",
    "uuid": "UUID",
    "email": "TEXT",
}

# IR type -> TypeScript type. `decimal` is `string` for the reason above.
TS_TYPES: dict[str, str] = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "decimal": "string",
    "boolean": "boolean",
    "text": "string",
    "array": "unknown[]",
    "object": "Record<string, unknown>",
    "datetime": "string",
    "date": "string",
    "uuid": "string",
    "email": "string",
}

# Defaults applied when a `decimal` field declares no precision/scale. 18 total
# digits with 2 fractional is the common money shape and holds values up to
# 10^16, comfortably beyond any realistic currency amount.
DEFAULT_DECIMAL_PRECISION = 18
DEFAULT_DECIMAL_SCALE = 2


def py_type(ir_type: str) -> str:
    """Map an IR type to a Python annotation, defaulting to `Any`.

    An unknown type is a contract the meta-schema should have rejected, so
    `Any` here is a last resort rather than a supported path.
    """
    return PY_TYPES.get(ir_type, "Any")


def ts_type(ir_type: str) -> str:
    """Map an IR type to a TypeScript type, defaulting to `unknown`."""
    return TS_TYPES.get(ir_type, "unknown")


def pg_column_type(ir_type: str, constraints: dict | None = None) -> str:
    """Map an IR type to a PostgreSQL column type.

    `decimal` is parameterised from the field's constraints so an amount column
    gets a real precision rather than unbounded NUMERIC:

        constraints: {precision: 12, scale: 4}  ->  NUMERIC(12, 4)

    Args:
        ir_type: The IR field type.
        constraints: The field's `constraints` mapping, if any.

    Returns:
        A PostgreSQL type expression.
    """
    if ir_type == "decimal":
        c = constraints or {}
        precision = c.get("precision", DEFAULT_DECIMAL_PRECISION)
        scale = c.get("scale", DEFAULT_DECIMAL_SCALE)
        return f"NUMERIC({int(precision)}, {int(scale)})"
    return PG_TYPES.get(ir_type, "TEXT")


def required_imports(ir_types: object) -> list[str]:
    """Collect the deduplicated, sorted import lines for a set of IR types.

    Args:
        ir_types: Any iterable of IR type strings.

    Returns:
        Sorted unique import statements needed to annotate those types.
    """
    needed = {
        PY_TYPE_IMPORTS[py]
        for t in ir_types
        if (py := PY_TYPES.get(t)) in PY_TYPE_IMPORTS
    }
    return sorted(needed)

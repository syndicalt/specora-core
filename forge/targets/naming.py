"""Canonical identifier derivation and quoting for all generators.

Every generated identifier — Python class, Python function, module filename,
SQL table, SQL column — is derived here and nowhere else. Before this module
existed, `_to_class` and `_pluralize` were reimplemented per generator, and
identifiers were built by ad-hoc f-string substitution. That produced two
whole classes of defect:

  1. Cross-domain collisions. Identifiers derived from `entity.name` alone
     mean `entity/billing/account` and `entity/support/account` both yield
     class `Account`, table `accounts`, and module `routes_account.py`. The
     second silently overwrites the first.

  2. Invalid identifiers. Deriving a function name from a URL path via
     `path.replace('/', '_')` emits `post_order_{id}_archive` for the path
     `/{id}/archive` — not a legal Python identifier, so the generated
     module fails to parse and the application cannot boot.

Both are prevented structurally here: `class_name`/`table_name`/`module_slug`
take the owning domain into account, and `py_identifier` guarantees its output
is a valid, non-reserved Python identifier for *any* input string.

SQL quoting lives here too. `sql_ident` and `sql_literal` are the only
sanctioned ways to put a name or a value into generated DDL — a field legally
named `order` or a default legally containing an apostrophe must not be able
to produce broken or injectable SQL.
"""

from __future__ import annotations

import keyword
import re

# Postgres folds unquoted identifiers to lowercase and rejects reserved words
# used bare. We always quote, so this set is advisory only — used to warn
# contract authors rather than to reject their field names.
SQL_RESERVED = frozenset(
    """
    all analyse analyze and any array as asc asymmetric authorization binary both
    case cast check collate collation column concurrently constraint create cross
    current_catalog current_date current_role current_schema current_time
    current_timestamp current_user default deferrable desc distinct do else end
    except false fetch for foreign freeze from full grant group having ilike in
    initially inner intersect into is isnull join lateral leading left like limit
    localtime localtimestamp natural not notnull null offset on only or order outer
    overlaps placing primary references returning right select session_user similar
    some symmetric table tablesample then to trailing true union unique user using
    variadic verbose when where window with
    """.split()
)

# Postgres truncates identifiers at 63 bytes, which turns two distinct long
# names into one silent collision.
PG_MAX_IDENTIFIER_BYTES = 63


def py_identifier(raw: str, *, prefix: str = "x") -> str:
    """Coerce an arbitrary string into a valid, non-reserved Python identifier.

    Guarantees for any input:
      - contains only [A-Za-z0-9_]
      - does not start with a digit
      - is not a Python keyword or soft keyword
      - is non-empty

    Args:
        raw: Arbitrary text (a URL path, a contract field name, ...).
        prefix: Prepended when `raw` would otherwise start with a digit or
            be empty.

    Returns:
        A legal Python identifier.

    Examples:
        >>> py_identifier("/{id}/archive")
        'id_archive'
        >>> py_identifier("2fa")
        'x2fa'
        >>> py_identifier("class")
        'class_'
    """
    cleaned = re.sub(r"\W+", "_", raw).strip("_")
    cleaned = re.sub(r"_{2,}", "_", cleaned)

    if not cleaned:
        cleaned = prefix
    if cleaned[0].isdigit():
        cleaned = f"{prefix}{cleaned}"
    if keyword.iskeyword(cleaned) or keyword.issoftkeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def pascal_case(name: str) -> str:
    """Convert a snake_case name to PascalCase.

    Unlike `"".join(p.capitalize() for p in name.split("_"))`, this preserves
    digits as their own segment boundary and never emits an empty segment for
    doubled underscores.
    """
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def pluralize(name: str) -> str:
    """Derive a plural form for table naming.

    Deliberately simple and deterministic — a real pluralizer would introduce
    a dependency and non-obvious behaviour for a name that only has to be a
    stable, unique identifier. Uniqueness is enforced separately by
    `forge.ir.semantic`; this function only has to be consistent.
    """
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    if name.endswith(("s", "sh", "ch", "x", "z")):
        return name + "es"
    return name + "s"


def class_name(name: str, domain: str, *, multi_domain: bool) -> str:
    """Derive the Python class stem for an entity.

    In a single-domain build this is just PascalCase(name), so existing
    single-domain output is unchanged. In a multi-domain build the domain is
    prepended, because `entity/billing/account` and `entity/support/account`
    must not both compile to `Account`.

    Examples:
        >>> class_name("api_key", "saas", multi_domain=False)
        'ApiKey'
        >>> class_name("account", "billing", multi_domain=True)
        'BillingAccount'
    """
    stem = pascal_case(name)
    return f"{pascal_case(domain)}{stem}" if multi_domain else stem


def table_name(name: str, domain: str, *, multi_domain: bool) -> str:
    """Derive the SQL table name for an entity.

    Multi-domain builds are prefixed with the domain to keep tables distinct.
    Prefer this over a Postgres schema per domain for now: schemas would also
    require `search_path` handling in every generated repository.
    """
    plural = pluralize(name)
    return f"{domain}_{plural}" if multi_domain else plural


def module_slug(name: str, domain: str, *, multi_domain: bool) -> str:
    """Derive the module filename stem for an entity's routes.

    Multi-domain builds are prefixed so `routes_billing_account.py` and
    `routes_support_account.py` are distinct files rather than one file
    written twice.
    """
    stem = py_identifier(name)
    return f"{domain}_{stem}" if multi_domain else stem


def repo_accessor(name: str, domain: str, *, multi_domain: bool) -> str:
    """Derive the repository factory function name (`get_<slug>_repo`)."""
    return f"get_{module_slug(name, domain, multi_domain=multi_domain)}_repo"


def sql_ident(name: str) -> str:
    """Quote a SQL identifier for safe interpolation into generated DDL/DML.

    Always quotes. A field legally named `order`, `group`, or `limit` is a
    reserved word that breaks unquoted SQL, and quoting unconditionally is
    simpler to reason about than quoting selectively. Embedded double quotes
    are escaped by doubling, per the SQL standard.

    Raises:
        ValueError: If the name is empty or exceeds Postgres' 63-byte
            identifier limit, where silent truncation would create a
            collision between two distinct names.
    """
    if not name:
        raise ValueError("SQL identifier cannot be empty")
    encoded = name.encode("utf-8")
    if len(encoded) > PG_MAX_IDENTIFIER_BYTES:
        raise ValueError(
            f"SQL identifier {name!r} is {len(encoded)} bytes, exceeding the "
            f"PostgreSQL limit of {PG_MAX_IDENTIFIER_BYTES}. Postgres would "
            f"truncate it, risking a silent collision with another identifier."
        )
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def sql_literal(value: object) -> str:
    """Render a Python value as a SQL literal for a DDL DEFAULT clause.

    Contract-authored defaults reach DDL through here. Because contracts are
    themselves often LLM-authored, a default containing an apostrophe (or
    worse) must not be able to corrupt the schema — `DEFAULT 'O'Brien'` is a
    syntax error, and the same interpolation is an injection vector.

    Raises:
        TypeError: For values with no safe, unambiguous SQL rendering. Failing
            loudly at generation time is correct: the alternative is emitting
            SQL that breaks at deploy time.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (list, dict)):
        import json

        return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"
    raise TypeError(
        f"Cannot render {type(value).__name__} as a SQL literal: {value!r}. "
        f"Use a string, number, boolean, list, or mapping in the contract."
    )

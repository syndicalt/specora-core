"""Skip markers for tests that need an optional extra.

`pip install -e '.[dev]'` is the documented development install and deliberately
does not pull in `[llm]` or `[healer]`. A test that needs one of those SDKs must
*skip* under that install rather than fail: a red suite has to mean the product
is broken, not that an optional package is absent.
"""

from __future__ import annotations

import importlib.util

import pytest


def has_module(name: str) -> bool:
    """Report whether *name* is importable, without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def requires(*modules: str) -> pytest.MarkDecorator:
    """Mark a test as needing every module in *modules* to be installed."""
    missing = sorted(name for name in modules if not has_module(name))
    return pytest.mark.skipif(
        bool(missing),
        reason=f"optional dependency not installed: {', '.join(missing)}",
    )

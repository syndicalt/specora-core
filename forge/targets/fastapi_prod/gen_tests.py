"""Generate black-box pytest tests from route contracts.

NOTE: This is a minimal stub. Full implementation is tracked in Task 7.
"""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile


def generate_tests(ir: DomainIR) -> list[GeneratedFile]:
    """Generate pytest test files for each entity's API routes.

    Returns an empty list until the full test generator is implemented (Task 7).
    """
    return []

"""IR passes — transformations that run after initial compilation.

Passes modify the IR to resolve cross-references, expand mixins,
infer defaults, and validate consistency. They run in a defined order:

    1. mixin_expansion    — Copy mixin fields into entities
    2. table_name_inference — Infer table names from entity names
    3. state_machine_binding — Bind workflow contracts to entities
    4. reference_resolution — Validate all entity references resolve

Each pass takes a DomainIR and returns a (possibly modified) DomainIR.
Passes must be idempotent — running them twice produces the same result.
"""

from __future__ import annotations

import logging

from forge.ir.model import DomainIR

logger = logging.getLogger(__name__)


def run_all_passes(ir: DomainIR) -> DomainIR:
    """Run all IR passes in order.

    Args:
        ir: The compiled DomainIR (pre-passes).

    Returns:
        The DomainIR after all passes have been applied.
    """
    from forge.ir.passes.mixin_expansion import expand_mixins
    from forge.ir.passes.table_name_inference import infer_table_names
    from forge.ir.passes.state_machine_binding import bind_state_machines
    from forge.ir.passes.reference_resolution import resolve_references

    passes = [
        ("mixin_expansion", expand_mixins),
        ("table_name_inference", infer_table_names),
        ("state_machine_binding", bind_state_machines),
        ("reference_resolution", resolve_references),
    ]

    for name, pass_fn in passes:
        logger.debug("Running IR pass: %s", name)
        ir = pass_fn(ir)
    logger.info("All IR passes complete")
    return ir

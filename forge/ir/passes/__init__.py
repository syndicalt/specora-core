"""IR passes — transformations that run after initial compilation.

Passes modify the IR to expand mixins and infer defaults. They run in a
defined order:

    1. mixin_expansion      — Copy mixin fields into entities
    2. table_name_inference — Infer table names from entity names
    3. state_machine_binding — Bind workflow contracts to entities
    4. base_path_inference  — Derive route base paths from their entity

Each pass takes a DomainIR and returns a (possibly modified) DomainIR.
Passes must be idempotent — running them twice produces the same result.

Passes transform; they do not validate. Everything that can be wrong across
contracts is reported by `forge.ir.semantic.validate_semantics`, which runs
after all passes and raises. A pass that logs a warning about a condition
semantic validation already rejects is not a safety net — it is noise emitted
milliseconds before the build fails anyway.
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
    from forge.ir.passes.base_path_inference import infer_base_paths
    from forge.ir.passes.mixin_expansion import expand_mixins
    from forge.ir.passes.state_machine_binding import bind_state_machines
    from forge.ir.passes.table_name_inference import infer_table_names

    passes = [
        ("mixin_expansion", expand_mixins),
        ("table_name_inference", infer_table_names),
        ("state_machine_binding", bind_state_machines),
        ("base_path_inference", infer_base_paths),
    ]

    for name, pass_fn in passes:
        logger.debug("Running IR pass: %s", name)
        ir = pass_fn(ir)
    logger.info("All IR passes complete")
    return ir

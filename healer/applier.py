"""Apply healer fixes with validation and rollback."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any

import yaml

from forge.diff.models import DiffOrigin
from forge.diff.store import DiffStore
from forge.diff.tracker import create_diff
from forge.parser.validator import validate_contract
from healer.models import HealerProposal

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    success: bool
    error: str = ""
    notes: list[str] = dc_field(default_factory=list)


def strip_internal_keys(value: Any) -> Any:
    """Deep-copy *value* without any key whose name starts with an underscore.

    The contract loader injects bookkeeping such as ``_source_path`` — an
    absolute path on whichever machine loaded the contract — into every loaded
    dict, and the validator ignores underscore-prefixed keys, so a proposal
    built from a loaded contract carries them through validation and then gets
    serialised straight into the user's repository. Stripping here is
    deliberately defensive: the applier is the last point before bytes reach a
    file the user owns, so it must not depend on the loader's behaviour.
    """
    if isinstance(value, dict):
        return {
            k: strip_internal_keys(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k.startswith("_"))
        }
    if isinstance(value, list):
        return [strip_internal_keys(v) for v in value]
    return value


def _dump(contract: dict) -> str:
    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)


def apply_fix(
    proposal: HealerProposal,
    contract_path: Path,
    diff_root: Path = Path(".forge/diffs"),
    ticket_id: str = "",
    actor: str = "",
) -> ApplyResult:
    """Write a validated proposal to *contract_path* atomically.

    Ordering matters and used to be wrong. The previous implementation wrote
    the new contract first, validated afterwards, and restored from a string
    held in a local variable if validation failed. That made the only copy of a
    valid contract live in one process's memory: an OOM kill or container
    restart between the two writes destroyed it permanently, and because
    ``write_text`` truncates in place, a crash mid-write left truncated YAML on
    disk. It also validated the in-memory dict rather than the bytes it wrote,
    so a value that does not survive the YAML round trip passed validation
    while leaving an unloadable file.

    So: validate, serialise, round-trip the serialised bytes back through the
    parser and validate *those*, then swap the file in with ``os.replace``,
    which is atomic. The original file is never opened for writing.
    """
    after = strip_internal_keys(proposal.after)
    # before is kept verbatim: it is only ever a diff snapshot, and the audit
    # trail should record what was actually on disk — including contamination
    # this apply is repairing.
    before = proposal.before

    notes: list[str] = []
    if after != proposal.after:
        # An earlier version of this applier wrote the loader's _source_path
        # into contract files. Such a file now fails validation outright, so
        # the healer repairs it here rather than refusing to touch it: a file
        # corrupted by our own bug is exactly what a self-healing system exists
        # to fix. The repair is recorded, never silent.
        notes.append("Removed internal underscore-prefixed keys (e.g. _source_path)")
        logger.info("Stripped internal keys from proposal for %s", proposal.contract_fqn)

    errors = validate_contract(after)
    real_errors = [e for e in errors if e.severity == "error"]
    if real_errors:
        error_msgs = "; ".join(e.message for e in real_errors[:3])
        logger.warning("Fix rejected before writing: %s", error_msgs)
        return ApplyResult(success=False, error=error_msgs, notes=notes)

    if not contract_path.exists():
        return ApplyResult(
            success=False, error=f"Contract file not found: {contract_path}", notes=notes
        )

    serialized = _dump(after)

    try:
        reloaded = yaml.safe_load(serialized)
    except yaml.YAMLError as exc:
        return ApplyResult(
            success=False, error=f"Serialized contract is not valid YAML: {exc}", notes=notes
        )

    reload_errors = [e for e in validate_contract(reloaded or {}) if e.severity == "error"]
    if reload_errors:
        error_msgs = "; ".join(e.message for e in reload_errors[:3])
        logger.warning("Serialized contract failed validation after reload: %s", error_msgs)
        return ApplyResult(
            success=False, error=f"Round-trip validation failed: {error_msgs}", notes=notes
        )

    if reloaded != after:
        return ApplyResult(
            success=False,
            error="Serialized contract does not round-trip; refusing to write.",
            notes=notes,
        )

    try:
        _atomic_write(contract_path, serialized)
    except OSError as exc:
        logger.error("Failed to write %s: %s", contract_path, exc)
        return ApplyResult(success=False, error=f"Write failed: {exc}", notes=notes)

    diff = create_diff(
        contract_fqn=proposal.contract_fqn,
        before=before,
        after=after,
        origin=DiffOrigin.HEALER,
        origin_detail=_origin_detail(ticket_id, actor),
        reason=proposal.explanation,
    )
    store = DiffStore(root=diff_root)
    store.save(diff)

    logger.info(
        "Applied fix to %s (%s) approved by %s",
        proposal.contract_fqn,
        proposal.method,
        actor or "unattributed",
    )
    return ApplyResult(success=True, notes=notes)


def _origin_detail(ticket_id: str, actor: str) -> str:
    """Audit trail entry: which ticket, and which principal approved it.

    A public endpoint that mutates the source of truth has to record who, not
    only what — ``healer:ticket-<id>`` alone cannot answer "who approved this".
    """
    subject = f"healer:ticket-{ticket_id}" if ticket_id else "healer:direct"
    return f"{subject};actor={actor}" if actor else subject


def _atomic_write(path: Path, content: str) -> None:
    """Replace *path* with *content* in one indivisible step.

    The temp file is created in the same directory so ``os.replace`` stays
    within a filesystem, and is fsynced before the swap so a power loss cannot
    leave the new name pointing at unwritten blocks.
    """
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

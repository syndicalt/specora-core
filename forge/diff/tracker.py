"""Contract diff tracker — computes structural diffs between contract versions.

The tracker uses the `deepdiff` library to compute fine-grained structural
differences between two versions of a contract. It normalizes deepdiff's
output into our FieldChange model with JSONPath-like paths.

The tracker is the engine that powers the diff system:
  1. Compare two contract dicts (before and after)
  2. Identify every field that was added, removed, or modified
  3. Compute the change path in dot notation
  4. Package everything into a ContractDiff record

Usage:
    from forge.diff.tracker import compute_diff, create_diff
    from forge.diff.models import DiffOrigin

    # Compute raw field changes
    changes = compute_diff(old_contract, new_contract)

    # Create a complete diff record
    diff = create_diff(
        contract_fqn="entity/itsm/incident",
        before=old_contract,
        after=new_contract,
        origin=DiffOrigin.HEALER,
        origin_detail="healer:bug-1234",
        reason="Added validation for deleted references",
    )
"""

from __future__ import annotations

import re
from typing import Any

from deepdiff import DeepDiff

from forge.diff.models import ContractDiff, DiffOrigin, FieldChange, hash_contract


def _deepdiff_path_to_dot(path: str) -> str:
    """Convert a deepdiff path to dot notation.

    DeepDiff uses paths like:
        root['spec']['fields']['priority']['type']
        root['spec']['transitions']['new'][0]

    We convert these to:
        spec.fields.priority.type
        spec.transitions.new[0]

    Args:
        path: DeepDiff-style path string.

    Returns:
        Dot-notation path string.
    """
    # Remove 'root' prefix
    result = path.replace("root", "", 1)
    # Convert ['key'] to .key
    result = re.sub(r"\['([^']+)'\]", r".\1", result)
    # Convert [N] array indices (leave as-is but attach to previous segment)
    result = re.sub(r"\[(\d+)\]", r"[\1]", result)
    # Strip leading dot
    return result.lstrip(".")


def _extract_value(data: dict, path: str) -> Any:
    """Extract a value from a nested dict using dot notation.

    Handles both dict keys and array indices:
        "spec.fields.priority.type" -> data["spec"]["fields"]["priority"]["type"]
        "spec.transitions.new[0]"   -> data["spec"]["transitions"]["new"][0]

    Args:
        data: The nested dict to extract from.
        path: Dot-notation path.

    Returns:
        The value at the path, or None if not found.
    """
    current = data
    # Split on dots but preserve array indices
    parts = re.split(r"\.(?![^\[]*\])", path)
    for part in parts:
        if current is None:
            return None
        # Check for array index: "key[N]"
        match = re.match(r"^(.+?)\[(\d+)\]$", part)
        if match:
            key, idx = match.group(1), int(match.group(2))
            current = current.get(key) if isinstance(current, dict) else None
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            current = current.get(part) if isinstance(current, dict) else None
    return current


def compute_diff(before: dict, after: dict) -> list[FieldChange]:
    """Compute the structural diff between two contract versions.

    Uses DeepDiff to find all differences, then normalizes them into
    FieldChange objects with dot-notation paths.

    Handles four categories of changes:
        - dictionary_item_added:   New fields/keys added
        - dictionary_item_removed: Fields/keys removed
        - values_changed:          Scalar values modified
        - type_changes:            Value type changed (e.g., string -> integer)

    Also handles:
        - iterable_item_added:     Items added to arrays
        - iterable_item_removed:   Items removed from arrays

    Args:
        before: The contract dict before the change.
        after: The contract dict after the change.

    Returns:
        List of FieldChange objects describing every change.
    """
    diff = DeepDiff(before, after, ignore_order=False, verbose_level=2)
    changes: list[FieldChange] = []

    # New fields/keys added
    for path in diff.get("dictionary_item_added", {}):
        dot_path = _deepdiff_path_to_dot(path)
        changes.append(
            FieldChange(
                path=dot_path,
                old_value=None,
                new_value=_extract_value(after, dot_path),
                change_type="added",
            )
        )

    # Fields/keys removed
    for path in diff.get("dictionary_item_removed", {}):
        dot_path = _deepdiff_path_to_dot(path)
        changes.append(
            FieldChange(
                path=dot_path,
                old_value=_extract_value(before, dot_path),
                new_value=None,
                change_type="removed",
            )
        )

    # Scalar values changed
    for path, change in diff.get("values_changed", {}).items():
        dot_path = _deepdiff_path_to_dot(path)
        changes.append(
            FieldChange(
                path=dot_path,
                old_value=change.get("old_value"),
                new_value=change.get("new_value"),
                change_type="modified",
            )
        )

    # Type changes (e.g., string -> integer)
    for path, change in diff.get("type_changes", {}).items():
        dot_path = _deepdiff_path_to_dot(path)
        changes.append(
            FieldChange(
                path=dot_path,
                old_value=change.get("old_value"),
                new_value=change.get("new_value"),
                change_type="type_changed",
            )
        )

    # Array items added
    for path in diff.get("iterable_item_added", {}):
        dot_path = _deepdiff_path_to_dot(path)
        changes.append(
            FieldChange(
                path=dot_path,
                old_value=None,
                new_value=_extract_value(after, dot_path),
                change_type="added",
            )
        )

    # Array items removed
    for path in diff.get("iterable_item_removed", {}):
        dot_path = _deepdiff_path_to_dot(path)
        changes.append(
            FieldChange(
                path=dot_path,
                old_value=_extract_value(before, dot_path),
                new_value=None,
                change_type="removed",
            )
        )

    return changes


def create_diff(
    contract_fqn: str,
    before: dict,
    after: dict,
    origin: DiffOrigin,
    reason: str,
    origin_detail: str = "",
) -> ContractDiff:
    """Create a complete ContractDiff record.

    Computes the structural diff, hashes both versions, and packages
    everything into a ContractDiff with full before/after snapshots.

    Args:
        contract_fqn: Fully Qualified Name (e.g., "entity/itsm/incident").
        before: The contract dict before the change.
        after: The contract dict after the change.
        origin: Who proposed this change (human, healer, advisor, factory).
        reason: Human-readable explanation of WHY this change was made.
        origin_detail: Specific identifier (e.g., "healer:bug-1234").

    Returns:
        A ContractDiff record ready to be stored.
    """
    return ContractDiff(
        contract_fqn=contract_fqn,
        origin=origin,
        origin_detail=origin_detail,
        reason=reason,
        changes=compute_diff(before, after),
        before_hash=hash_contract(before),
        after_hash=hash_contract(after),
        before_snapshot=before,
        after_snapshot=after,
    )

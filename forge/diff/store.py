"""Contract diff store — persists and queries contract diffs.

The diff store is a file-based storage system that persists ContractDiff
records to `.forge/diffs/`. Each diff is stored as a separate JSON file,
and an index file maps contract FQNs to their diff IDs for fast lookup.

Storage layout:
    .forge/diffs/
        index.json              — Maps FQN -> list of diff IDs
        {diff_id}.json          — Individual diff records

The store supports:
    - Saving new diffs
    - Querying by contract FQN, origin, or time range
    - Retrieving the full evolution history of a contract
    - Formatting diff history as context for LLM consumption

Usage:
    from forge.diff.store import DiffStore
    from forge.diff.models import ContractDiff

    store = DiffStore(root=Path(".forge/diffs"))
    store.save(diff)

    # Query
    history = store.get_history("entity/itsm/incident")
    recent = store.list_diffs(since=datetime(2026, 4, 1))

    # LLM context
    context = store.format_for_llm("entity/itsm/incident", n=5)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from forge.diff.models import ContractDiff, DiffOrigin

logger = logging.getLogger(__name__)


class DiffStore:
    """File-based storage for contract diffs.

    Diffs are stored individually as JSON files and indexed by contract FQN.
    The index is loaded lazily and updated on every save.

    Attributes:
        root: The directory where diffs are stored (.forge/diffs/).
    """

    def __init__(self, root: Path):
        """Initialize the diff store.

        Args:
            root: Directory path for diff storage. Created if it doesn't exist.
        """
        self.root = Path(root)
        self._index: Optional[dict[str, list[str]]] = None

    def _ensure_dir(self) -> None:
        """Create the storage directory if it doesn't exist."""
        self.root.mkdir(parents=True, exist_ok=True)

    def _index_path(self) -> Path:
        """Path to the index file."""
        return self.root / "index.json"

    def _load_index(self) -> dict[str, list[str]]:
        """Load the index from disk, or return empty dict if not found."""
        if self._index is not None:
            return self._index
        path = self._index_path()
        if path.exists():
            try:
                self._index = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt diff index, rebuilding")
                self._index = {}
        else:
            self._index = {}
        return self._index

    def _save_index(self) -> None:
        """Write the index to disk."""
        self._ensure_dir()
        self._index_path().write_text(
            json.dumps(self._load_index(), indent=2), encoding="utf-8"
        )

    def save(self, diff: ContractDiff) -> None:
        """Persist a diff record.

        Saves the diff as a JSON file and updates the index to include
        the new diff ID under its contract FQN.

        Args:
            diff: The ContractDiff to save.
        """
        self._ensure_dir()

        # Write the diff file
        diff_path = self.root / f"{diff.id}.json"
        diff_data = diff.model_dump(mode="json")
        diff_path.write_text(json.dumps(diff_data, indent=2, default=str), encoding="utf-8")

        # Update the index
        index = self._load_index()
        fqn_diffs = index.setdefault(diff.contract_fqn, [])
        if diff.id not in fqn_diffs:
            fqn_diffs.append(diff.id)
        self._save_index()

        logger.info(
            "Saved diff %s for %s (%s: %s)",
            diff.id[:8],
            diff.contract_fqn,
            diff.origin.value,
            diff.reason[:60],
        )

    def get_diff(self, diff_id: str) -> Optional[ContractDiff]:
        """Retrieve a specific diff by ID.

        Args:
            diff_id: The UUID of the diff to retrieve.

        Returns:
            The ContractDiff, or None if not found.
        """
        diff_path = self.root / f"{diff_id}.json"
        if not diff_path.exists():
            return None
        try:
            data = json.loads(diff_path.read_text(encoding="utf-8"))
            return ContractDiff(**data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to load diff %s: %s", diff_id, e)
            return None

    def get_history(self, contract_fqn: str) -> list[ContractDiff]:
        """Get the full change history for a contract, ordered by timestamp.

        Args:
            contract_fqn: Fully Qualified Name (e.g., "entity/itsm/incident").

        Returns:
            List of ContractDiff records, oldest first.
        """
        index = self._load_index()
        diff_ids = index.get(contract_fqn, [])
        diffs = []
        for did in diff_ids:
            diff = self.get_diff(did)
            if diff:
                diffs.append(diff)
        diffs.sort(key=lambda d: d.timestamp)
        return diffs

    def list_diffs(
        self,
        contract_fqn: Optional[str] = None,
        origin: Optional[DiffOrigin] = None,
        since: Optional[datetime] = None,
    ) -> list[ContractDiff]:
        """Query diffs with optional filters.

        Args:
            contract_fqn: Filter by contract FQN. None for all contracts.
            origin: Filter by change origin. None for all origins.
            since: Filter by timestamp (diffs after this time). None for all.

        Returns:
            List of matching ContractDiff records, newest first.
        """
        index = self._load_index()

        # Determine which diff IDs to load
        if contract_fqn:
            diff_ids = index.get(contract_fqn, [])
        else:
            diff_ids = [did for ids in index.values() for did in ids]

        # Load and filter
        results = []
        for did in diff_ids:
            diff = self.get_diff(did)
            if diff is None:
                continue
            if origin and diff.origin != origin:
                continue
            if since and diff.timestamp < since:
                continue
            results.append(diff)

        results.sort(key=lambda d: d.timestamp, reverse=True)
        return results

    def format_for_llm(self, contract_fqn: str, n: int = 10) -> str:
        """Format recent diffs as text context for LLM consumption.

        Produces a structured text summary of the most recent changes
        to a contract. This is designed to be injected into LLM prompts
        so the Healer/Advisor has context about contract evolution.

        The format is designed for readability by both humans and LLMs:
          - Each diff shows the timestamp, origin, reason, and changes
          - Field changes show the path and old/new values
          - The most recent diff is first

        Args:
            contract_fqn: Fully Qualified Name of the contract.
            n: Maximum number of recent diffs to include (default: 10).

        Returns:
            Formatted text string, or a message if no history exists.
        """
        history = self.get_history(contract_fqn)
        if not history:
            return f"No change history for {contract_fqn}"

        # Take the most recent N
        recent = history[-n:]
        recent.reverse()  # Most recent first

        lines = [
            f"# Change History: {contract_fqn}",
            f"# {len(history)} total changes, showing {len(recent)} most recent",
            "",
        ]

        for i, diff in enumerate(recent, 1):
            ts = diff.timestamp.strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"## Change {i} — {ts}")
            lines.append(f"Origin: {diff.origin.value} ({diff.origin_detail})")
            lines.append(f"Reason: {diff.reason}")
            lines.append(f"Changes ({len(diff.changes)} fields):")

            for change in diff.changes:
                if change.change_type == "added":
                    lines.append(f"  + {change.path}: {_format_value(change.new_value)}")
                elif change.change_type == "removed":
                    lines.append(f"  - {change.path}: {_format_value(change.old_value)}")
                else:
                    lines.append(
                        f"  ~ {change.path}: "
                        f"{_format_value(change.old_value)} -> {_format_value(change.new_value)}"
                    )

            lines.append("")

        return "\n".join(lines)

    def count(self, contract_fqn: Optional[str] = None) -> int:
        """Count diffs, optionally filtered by contract FQN.

        Args:
            contract_fqn: Filter by FQN. None counts all diffs.

        Returns:
            Number of diffs.
        """
        index = self._load_index()
        if contract_fqn:
            return len(index.get(contract_fqn, []))
        return sum(len(ids) for ids in index.values())


def _format_value(value: object) -> str:
    """Format a value for display in diff output.

    Truncates long strings and serializes complex objects compactly.
    """
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"' if len(value) < 80 else f'"{value[:77]}..."'
    if isinstance(value, (dict, list)):
        s = json.dumps(value, separators=(",", ":"), default=str)
        return s if len(s) < 100 else s[:97] + "..."
    return str(value)

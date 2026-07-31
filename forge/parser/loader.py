"""Contract loader — discovers and loads .contract.yaml files.

The loader is the first stage of the compilation pipeline. It:

  1. Discovers all .contract.yaml files in a directory tree
  2. Loads each file as a YAML dict
  3. Validates the basic envelope structure (apiVersion, kind, metadata, spec)
  4. Computes the Fully Qualified Name (FQN) for each contract
  5. Also discovers and loads stdlib contracts from spec/stdlib/

Contract FQN format: kind/domain/name
  - kind is lowercased from the contract's `kind` field
  - domain comes from metadata.domain
  - name comes from metadata.name
  Example: "entity/itsm/incident", "mixin/stdlib/timestamped"

File convention: All contract files MUST use the .contract.yaml extension.
The loader ignores all other YAML files.

A loaded contract dict contains *only* what the file contains. The loader used
to inject `_source_path` into the dict for error reporting; because the Healer
round-trips contract dicts back through `yaml.dump`, that wrote an absolute
filesystem path from the healing machine into the user's contract file. Source
paths now travel in a sidecar map on `ContractSet` instead.

Usage:
    from forge.parser.loader import discover_contracts, load_contract, compute_fqn

    paths = discover_contracts(Path("domains/library"))
    for path in paths:
        contract = load_contract(path)
        fqn = compute_fqn(contract)

    # Or load everything, with source paths available alongside:
    contracts = load_all_contracts(Path("domains/library"))
    contracts["entity/library/book"]          # the contract dict
    contracts.path_for("entity/library/book") # str | None
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# The file extension that identifies contract files
CONTRACT_EXTENSION = ".contract.yaml"


def discover_contracts(root: Path, include_stdlib: bool = True) -> list[Path]:
    """Recursively discover all contract files under a directory.

    Scans the given directory tree for files ending in .contract.yaml.
    Optionally includes the standard library contracts from spec/stdlib/.

    Args:
        root: The root directory to scan (e.g., Path("domains/library")).
        include_stdlib: If True, also discover stdlib contracts.
            The stdlib path is resolved relative to the specora-core
            project root (found by walking up from this file).

    Returns:
        List of Path objects pointing to discovered contract files,
        sorted by path for deterministic ordering.
    """
    root = Path(root).resolve()
    paths: list[Path] = []

    if root.is_dir():
        paths.extend(sorted(root.rglob(f"*{CONTRACT_EXTENSION}")))
        logger.info("Discovered %d contracts in %s", len(paths), root)

    if include_stdlib:
        stdlib_root = _find_stdlib_root()
        if stdlib_root and stdlib_root.is_dir():
            stdlib_paths = sorted(stdlib_root.rglob(f"*{CONTRACT_EXTENSION}"))
            logger.info("Discovered %d stdlib contracts in %s", len(stdlib_paths), stdlib_root)
            paths.extend(stdlib_paths)

    return paths


def _find_stdlib_root() -> Optional[Path]:
    """Locate the spec/stdlib/ directory.

    Walks up from this file's location to find the project root,
    then returns spec/stdlib/ if it exists.
    """
    current = Path(__file__).resolve().parent
    # Walk up to find the project root (where spec/ lives)
    for _ in range(10):
        candidate = current / "spec" / "stdlib"
        if candidate.is_dir():
            return candidate
        current = current.parent
    return None


def load_contract(path: Path) -> dict:
    """Load a contract file from disk.

    Reads the YAML file and returns the raw dict. Does NOT validate
    against meta-schemas — that's the validator's job. Only checks
    that the file is valid YAML and has the basic envelope keys.

    Args:
        path: Path to the .contract.yaml file.

    Returns:
        The contract as a dict.

    Raises:
        ContractLoadError: If the file can't be read or parsed,
            or is missing required envelope keys.
    """
    path = Path(path).resolve()

    if not path.exists():
        raise ContractLoadError(f"Contract file not found: {path}")

    if not path.name.endswith(CONTRACT_EXTENSION):
        raise ContractLoadError(
            f"Contract files must end with {CONTRACT_EXTENSION}, got: {path.name}"
        )

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ContractLoadError(f"Cannot read {path}: {e}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ContractLoadError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ContractLoadError(
            f"Contract must be a YAML mapping, got {type(data).__name__}: {path}"
        )

    # Check for required envelope keys
    missing = [k for k in ("apiVersion", "kind", "metadata", "spec") if k not in data]
    if missing:
        raise ContractLoadError(
            f"Contract {path} missing required envelope keys: {', '.join(missing)}"
        )

    # Validate basic metadata
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ContractLoadError(f"Contract {path}: metadata must be a mapping")
    if "name" not in metadata:
        raise ContractLoadError(f"Contract {path}: metadata.name is required")
    if "domain" not in metadata:
        raise ContractLoadError(f"Contract {path}: metadata.domain is required")

    return data


class ContractSet(dict):
    """FQN -> contract dict, with the file each contract came from alongside.

    Subclasses `dict` so every existing consumer (`contracts[fqn]`,
    `.items()`, `len()`) keeps working unchanged, while source paths live in a
    separate mapping instead of inside the contract dicts. Anything that
    serializes a contract — the Healer writing a healed contract back to disk,
    a diff, an LLM prompt — therefore cannot leak a build-machine path into a
    user's repository.

    Attributes:
        source_paths: FQN -> absolute file path, as strings.
    """

    def __init__(self, *args, source_paths: Optional[dict[str, str]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_paths: dict[str, str] = dict(source_paths or {})

    def path_for(self, fqn: str) -> Optional[str]:
        """Return the file a contract was loaded from, or None if unknown."""
        return self.source_paths.get(fqn)


def compute_fqn(contract: dict, source: Optional[Path] = None) -> str:
    """Compute the Fully Qualified Name for a contract.

    FQN format: kind/domain/name (all lowercase)
    Example: "entity/itsm/incident"

    Args:
        contract: A loaded contract dict.
        source: Optional path the contract came from, used only to make the
            error message actionable.

    Returns:
        The FQN string.

    Raises:
        ContractLoadError: If the contract is missing kind or metadata.
    """
    kind = str(contract.get("kind", "")).lower()
    metadata = contract.get("metadata") or {}
    domain = metadata.get("domain", "")
    name = metadata.get("name", "")

    if not kind or not domain or not name:
        raise ContractLoadError(
            f"Cannot compute FQN for {source or '<unknown>'}: "
            f"kind={kind!r}, domain={domain!r}, name={name!r}"
        )

    return f"{kind}/{domain}/{name}"


def load_all_contracts(root: Path, include_stdlib: bool = True) -> ContractSet:
    """Discover and load all contracts, returning a map of FQN -> contract.

    This is a convenience function that combines discover_contracts,
    load_contract, and compute_fqn into a single call.

    Args:
        root: The root directory to scan.
        include_stdlib: If True, include stdlib contracts.

    Returns:
        A ContractSet — a dict of FQN -> contract dict, plus `.source_paths`
        and `.path_for(fqn)` for the file each contract came from.

    Raises:
        ContractLoadError: If any contract fails to load or has a duplicate FQN.
    """
    paths = discover_contracts(root, include_stdlib=include_stdlib)
    contracts = ContractSet()

    for path in paths:
        contract = load_contract(path)
        fqn = compute_fqn(contract, source=path)

        if fqn in contracts:
            existing = contracts.path_for(fqn) or "<unknown>"
            raise ContractLoadError(f"Duplicate FQN '{fqn}': found in both {existing} and {path}")

        contracts[fqn] = contract
        contracts.source_paths[fqn] = str(path)
        logger.debug("Loaded %s from %s", fqn, path)

    logger.info("Loaded %d contracts total", len(contracts))
    return contracts


class ContractLoadError(Exception):
    """Raised when a contract file cannot be loaded or parsed."""

    pass

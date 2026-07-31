"""Contract validator — validates contracts against their meta-schemas.

The validator is the second stage of the compilation pipeline. After
the loader has parsed YAML into dicts, the validator checks each
contract against the meta-schema for its declared `kind`.

Meta-schemas are stored in spec/meta/ as YAML files that conform to
JSON Schema draft 2020-12. The jsonschema library validates contracts
against these meta-schemas.

The validator returns structured ValidationError objects instead of
raising exceptions, so the compiler can collect all errors before
reporting them.

Usage:
    from forge.parser.validator import validate_contract, validate_all

    errors = validate_contract(contract)
    if errors:
        for err in errors:
            print(f"{err.severity}: {err.path} — {err.message}")

    # Or validate a batch
    all_errors = validate_all(contracts)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

logger = logging.getLogger(__name__)

# Compiled validators, keyed by contract kind. A `Draft202012Validator` resolves
# and compiles its schema on construction, so building one per contract made
# validation cost scale with contract count for no benefit — one per kind is
# enough. `None` is cached too, to avoid re-globbing spec/meta/ for a kind that
# has no meta-schema.
#
# The whole cache sits behind one lock because the Healer is a long-lived
# multi-threaded HTTP service: the previous unsynchronized double-check let two
# threads build the registry concurrently and one of them discard its work.
_validator_cache: dict[str, Optional[Draft202012Validator]] = {}
_registry: Optional[Registry] = None
_cache_lock = threading.Lock()

# Valid contract kinds (must match envelope.meta.yaml)
VALID_KINDS = {"Entity", "Workflow", "Page", "Route", "Agent", "Mixin", "Infra"}


@dataclass
class ContractValidationError:
    """A single validation error found in a contract.

    Attributes:
        contract_fqn: The FQN of the contract with the error, if known.
        path: JSONPath-like location of the error within the contract.
            Example: "spec.fields.priority.type"
        message: Human-readable error description.
        severity: "error" for must-fix issues, "warning" for recommendations.
        source_path: The file path of the contract, if known.
    """

    contract_fqn: str = ""
    path: str = ""
    message: str = ""
    severity: str = "error"
    source_path: str = ""


def _find_meta_dir() -> Optional[Path]:
    """Locate the spec/meta/ directory."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = current / "spec" / "meta"
        if candidate.is_dir():
            return candidate
        current = current.parent
    return None


def _build_registry_locked() -> Registry:
    """Build a jsonschema Registry containing all meta-schemas.

    This allows $ref between meta-schemas (e.g., entity.meta.yaml
    referencing envelope.meta.yaml) to resolve locally without
    hitting the network.

    Caller must hold `_cache_lock`.

    Returns:
        A Registry with all meta-schemas registered by their $id.
    """
    global _registry
    if _registry is not None:
        return _registry

    meta_dir = _find_meta_dir()
    if not meta_dir:
        logger.warning("Cannot find spec/meta/ directory")
        _registry = Registry()
        return _registry

    resources: list[tuple[str, Resource]] = []
    for meta_path in sorted(meta_dir.glob("*.meta.yaml")):
        try:
            schema = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            schema_id = schema.get("$id", "")
            if schema_id:
                resource = Resource.from_contents(schema, default_specification=DRAFT202012)
                resources.append((schema_id, resource))
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to load meta-schema %s: %s", meta_path, e)

    _registry = Registry().with_resources(resources)
    logger.debug("Built meta-schema registry with %d schemas", len(resources))
    return _registry


def _get_validator(kind: str) -> Optional[Draft202012Validator]:
    """Return the compiled validator for a contract kind, building it once.

    Args:
        kind: The contract kind (e.g., "Entity", "Workflow").

    Returns:
        A compiled Draft202012Validator, or None if the kind has no
        meta-schema on disk.
    """
    with _cache_lock:
        if kind in _validator_cache:
            return _validator_cache[kind]

        validator: Optional[Draft202012Validator] = None
        meta_dir = _find_meta_dir()

        if not meta_dir:
            logger.warning("Cannot find spec/meta/ directory")
        else:
            meta_path = meta_dir / f"{kind.lower()}.meta.yaml"
            if not meta_path.exists():
                logger.warning("No meta-schema for kind '%s' at %s", kind, meta_path)
            else:
                try:
                    schema = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
                    validator = Draft202012Validator(schema, registry=_build_registry_locked())
                except (yaml.YAMLError, OSError) as e:
                    logger.error("Failed to load meta-schema %s: %s", meta_path, e)

        _validator_cache[kind] = validator
        return validator


def reset_schema_cache() -> None:
    """Drop the cached registry and validators so meta-schemas are re-read.

    The cache is process-global and otherwise never invalidated. That is fine
    for the CLI, which exits after one build, but the Healer is a long-lived
    service: without this, a meta-schema edited on disk is never picked up for
    the lifetime of the process. Call this after changing anything in
    spec/meta/.
    """
    global _registry
    with _cache_lock:
        _validator_cache.clear()
        _registry = None


def _jsonschema_path_to_dot(path: list) -> str:
    """Convert a jsonschema error path to dot notation.

    Args:
        path: The `absolute_path` deque from a jsonschema ValidationError.

    Returns:
        Dot-notation string (e.g., "spec.fields.priority.type").
    """
    parts = []
    for segment in path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(str(segment))
    return ".".join(parts) if parts else "<root>"


def validate_contract(
    contract: dict,
    fqn: Optional[str] = None,
    source_path: str = "<unknown>",
) -> list[ContractValidationError]:
    """Validate a single contract against its meta-schema.

    Performs two levels of validation:
    1. Envelope check: apiVersion, kind are valid
    2. Kind-specific validation: the full contract validates against
       the meta-schema for its declared kind

    Args:
        contract: A loaded contract dict.
        fqn: The contract's FQN, if the caller already knows it. Callers that
            loaded the contract by FQN should pass it rather than let this
            function re-derive it from metadata.
        source_path: The file the contract came from, for error reporting.
            Passed in rather than read out of the contract dict — the loader no
            longer injects paths into contracts.

    Returns:
        List of validation errors. Empty list means the contract is valid.
    """
    errors: list[ContractValidationError] = []

    kind = contract.get("kind", "")
    if fqn is None:
        metadata = contract.get("metadata") or {}
        fqn = f"{str(kind).lower()}/{metadata.get('domain', '?')}/{metadata.get('name', '?')}"

    # --- Envelope validation ---

    api_version = contract.get("apiVersion")
    if api_version != "specora.dev/v1":
        errors.append(
            ContractValidationError(
                contract_fqn=fqn,
                path="apiVersion",
                message=f"Expected 'specora.dev/v1', got '{api_version}'",
                source_path=source_path,
            )
        )

    if kind not in VALID_KINDS:
        errors.append(
            ContractValidationError(
                contract_fqn=fqn,
                path="kind",
                message=f"Invalid kind '{kind}'. Must be one of: {', '.join(sorted(VALID_KINDS))}",
                source_path=source_path,
            )
        )
        return errors  # Can't validate further without a valid kind

    # --- Kind-specific meta-schema validation ---

    validator = _get_validator(kind)
    if validator is None:
        errors.append(
            ContractValidationError(
                contract_fqn=fqn,
                path="kind",
                message=f"No meta-schema found for kind '{kind}'",
                severity="warning",
                source_path=source_path,
            )
        )
        return errors

    for error in sorted(validator.iter_errors(contract), key=lambda e: list(e.absolute_path)):
        errors.append(
            ContractValidationError(
                contract_fqn=fqn,
                path=_jsonschema_path_to_dot(error.absolute_path),
                message=error.message,
                source_path=source_path,
            )
        )

    return errors


def validate_all(contracts: dict[str, dict]) -> list[ContractValidationError]:
    """Validate all contracts in a collection.

    Args:
        contracts: Dict mapping FQN -> contract dict. A `ContractSet` from
            `load_all_contracts` also supplies each contract's source path.

    Returns:
        List of all validation errors across all contracts.
    """
    source_paths = getattr(contracts, "source_paths", {}) or {}
    all_errors: list[ContractValidationError] = []
    for fqn, contract in contracts.items():
        all_errors.extend(
            validate_contract(
                contract,
                fqn=fqn,
                source_path=source_paths.get(fqn, "<unknown>"),
            )
        )
    return all_errors

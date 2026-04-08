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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from jsonschema import Draft202012Validator, ValidationError as JSValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

logger = logging.getLogger(__name__)

# Cache for loaded meta-schemas
_meta_schema_cache: dict[str, dict] = {}

# Registry for resolving $ref between meta-schemas
_registry: Optional[Registry] = None

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


def _build_registry() -> Registry:
    """Build a jsonschema Registry containing all meta-schemas.

    This allows $ref between meta-schemas (e.g., entity.meta.yaml
    referencing envelope.meta.yaml) to resolve locally without
    hitting the network.

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
    for meta_path in meta_dir.glob("*.meta.yaml"):
        try:
            schema = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            schema_id = schema.get("$id", "")
            if schema_id:
                resource = Resource.from_contents(schema, default_specification=DRAFT202012)
                resources.append((schema_id, resource))
        except Exception as e:
            logger.warning("Failed to load meta-schema %s: %s", meta_path, e)

    _registry = Registry().with_resources(resources)
    logger.debug("Built meta-schema registry with %d schemas", len(resources))
    return _registry


def _load_meta_schema(kind: str) -> Optional[dict]:
    """Load the meta-schema for a given contract kind.

    Meta-schemas are cached after first load for performance.

    Args:
        kind: The contract kind (e.g., "Entity", "Workflow").

    Returns:
        The meta-schema as a dict, or None if not found.
    """
    if kind in _meta_schema_cache:
        return _meta_schema_cache[kind]

    meta_dir = _find_meta_dir()
    if not meta_dir:
        logger.warning("Cannot find spec/meta/ directory")
        return None

    filename = f"{kind.lower()}.meta.yaml"
    meta_path = meta_dir / filename

    if not meta_path.exists():
        logger.warning("No meta-schema for kind '%s' at %s", kind, meta_path)
        return None

    try:
        schema = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        _meta_schema_cache[kind] = schema
        return schema
    except (yaml.YAMLError, OSError) as e:
        logger.error("Failed to load meta-schema %s: %s", meta_path, e)
        return None


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


def validate_contract(contract: dict) -> list[ContractValidationError]:
    """Validate a single contract against its meta-schema.

    Performs two levels of validation:
    1. Envelope check: apiVersion, kind are valid
    2. Kind-specific validation: the full contract validates against
       the meta-schema for its declared kind

    Args:
        contract: A loaded contract dict.

    Returns:
        List of validation errors. Empty list means the contract is valid.
    """
    errors: list[ContractValidationError] = []
    source_path = contract.get("_source_path", "<unknown>")

    # Compute FQN for error reporting
    kind = contract.get("kind", "")
    metadata = contract.get("metadata", {})
    fqn = f"{kind.lower()}/{metadata.get('domain', '?')}/{metadata.get('name', '?')}"

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

    meta_schema = _load_meta_schema(kind)
    if meta_schema is None:
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

    # Strip internal fields before validation (e.g., _source_path)
    clean_contract = {k: v for k, v in contract.items() if not k.startswith("_")}

    # Use JSON Schema validation with registry for $ref resolution
    registry = _build_registry()
    validator = Draft202012Validator(meta_schema, registry=registry)
    for error in sorted(validator.iter_errors(clean_contract), key=lambda e: list(e.absolute_path)):
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
        contracts: Dict mapping FQN -> contract dict.

    Returns:
        List of all validation errors across all contracts.
    """
    all_errors: list[ContractValidationError] = []
    for fqn, contract in contracts.items():
        errors = validate_contract(contract)
        all_errors.extend(errors)
    return all_errors

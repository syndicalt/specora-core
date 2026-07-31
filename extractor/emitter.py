"""Emit contracts from an AnalysisReport using Factory emitters."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

import yaml

from extractor.models import AnalysisReport, ExtractedEntity, safe_contract_name
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.page_emitter import emit_page
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from forge.parser.validator import validate_contract
from forge.targets.naming import pluralize


class EmissionError(RuntimeError):
    """An extracted artifact could not be turned into a valid contract.

    Raised instead of writing the file. The output of the Extractor becomes
    somebody's source of truth, so a contract that does not satisfy its
    meta-schema must fail here rather than at the next `spc forge generate`.
    """


def emit_contracts(
    report: AnalysisReport,
    output_dir: Path,
    accepted_entities: list[ExtractedEntity] | None = None,
) -> list[Path]:
    """Emit contract YAML files from an AnalysisReport.

    Every contract is validated against its meta-schema before it is written,
    and every path is checked to be inside `output_dir`.

    Returns:
        The list of written file paths.

    Raises:
        EmissionError: if any contract fails validation, or if a name would
            place a file outside `output_dir`.
    """
    entities = accepted_entities if accepted_entities is not None else report.entities
    domain = safe_contract_name(report.domain, fallback="extracted")
    root = output_dir.resolve()
    written: list[Path] = []

    workflow_by_entity = {
        safe_contract_name(wf.entity_name): safe_contract_name(wf.name) for wf in report.workflows
    }

    used_collection_names: set[str] = set()
    emitted_entities = {safe_contract_name(e.name) for e in entities}

    for entity in entities:
        name = safe_contract_name(entity.name)
        data = entity.to_emitter_data()
        _drop_dangling_references(data["fields"], domain, emitted_entities)

        workflow_name = workflow_by_entity.get(name)
        if workflow_name:
            data["state_machine"] = f"workflow/{domain}/{workflow_name}"

        _write(root, "entities", name, partial(emit_entity, name, domain, data), written)

        collection = _unique(pluralize(name), used_collection_names)
        workflow_fqn = f"workflow/{domain}/{workflow_name}" if workflow_name else ""
        entity_fqn = f"entity/{domain}/{name}"

        _write(
            root,
            "routes",
            collection,
            partial(emit_route, collection, domain, entity_fqn, workflow_fqn),
            written,
        )
        _write(
            root,
            "pages",
            collection,
            partial(emit_page, collection, domain, entity_fqn, list(data["fields"])),
            written,
        )

    for wf in report.workflows:
        if safe_contract_name(wf.entity_name) not in emitted_entities:
            # Its entity was skipped in review; a workflow no entity binds is
            # dead weight in the domain.
            continue
        name = safe_contract_name(wf.name)
        data = wf.to_emitter_data()
        _write(root, "workflows", name, partial(emit_workflow, name, domain, data), written)

    return written


def _drop_dangling_references(fields: dict, domain: str, emitted: set[str]) -> None:
    """Demote a reference whose target entity is not being written.

    `emit_entity` copies every `references.entity` into `requires`, and the
    compiler rejects a `requires` naming a contract that does not exist. The
    target can be missing either because it was never extracted or because the
    user skipped it during review, so this has to run at write time.
    """
    for definition in fields.values():
        reference = definition.get("references")
        if not reference:
            continue
        target = reference.get("entity", "").rsplit("/", 1)[-1]
        if target not in emitted or reference.get("entity") != f"entity/{domain}/{target}":
            definition.pop("references")


def _unique(name: str, used: set[str]) -> str:
    candidate = name
    suffix = 2
    while candidate in used:
        candidate = f"{name}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _write(
    root: Path,
    kind_dir: str,
    name: str,
    build: Callable[[], str],
    written: list[Path],
) -> None:
    # The contract is built inside this boundary so that *any* failure to
    # produce one — a Factory emitter rejecting its own output, a name the
    # normalizer cannot fix — surfaces as EmissionError with nothing written,
    # rather than as whatever exception the emitter happens to raise today.
    try:
        yaml_str = build()
    except Exception as e:
        raise EmissionError(f"{kind_dir}/{name} could not be emitted — {e}") from e

    contract = yaml.safe_load(yaml_str)
    errors = [e for e in validate_contract(contract) if e.severity == "error"]
    if errors:
        detail = "; ".join(f"{e.path}: {e.message}" for e in errors[:3])
        raise EmissionError(f"{kind_dir}/{name} failed meta-schema validation — {detail}")

    path = (root / kind_dir / f"{name}.contract.yaml").resolve()
    if not path.is_relative_to(root):
        raise EmissionError(f"refusing to write {path}: outside the output directory {root}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_str, encoding="utf-8")
    written.append(path)

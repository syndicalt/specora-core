"""Emit contracts from an AnalysisReport using Factory emitters."""
from __future__ import annotations

from pathlib import Path

from factory.emitters.entity_emitter import emit_entity
from factory.emitters.page_emitter import emit_page
from factory.emitters.route_emitter import emit_route
from factory.emitters.workflow_emitter import emit_workflow
from forge.normalize import normalize_name
from extractor.models import AnalysisReport, ExtractedEntity


def emit_contracts(
    report: AnalysisReport,
    output_dir: Path,
    accepted_entities: list[ExtractedEntity] | None = None,
) -> list[Path]:
    """Emit contract YAML files from an AnalysisReport.

    Uses the Factory emitters (same normalization + validation).
    Returns list of written file paths.
    """
    entities = accepted_entities if accepted_entities is not None else report.entities
    domain = report.domain
    written: list[Path] = []

    # Emit entities
    for entity in entities:
        safe_name = normalize_name(entity.name)
        data = entity.to_emitter_data()

        # Add workflow reference if applicable
        for wf in report.workflows:
            if wf.entity_name == safe_name:
                data["state_machine"] = f"workflow/{domain}/{normalize_name(wf.name)}"
                break

        yaml_str = emit_entity(safe_name, domain, data)
        path = output_dir / "entities" / f"{safe_name}.contract.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_str, encoding="utf-8")
        written.append(path)

        # Emit route
        entity_fqn = f"entity/{domain}/{safe_name}"
        plural = safe_name + "s"
        workflow_fqn = ""
        for wf in report.workflows:
            if wf.entity_name == safe_name:
                workflow_fqn = f"workflow/{domain}/{normalize_name(wf.name)}"
                break

        route_yaml = emit_route(plural, domain, entity_fqn, workflow_fqn)
        route_path = output_dir / "routes" / f"{plural}.contract.yaml"
        route_path.parent.mkdir(parents=True, exist_ok=True)
        route_path.write_text(route_yaml, encoding="utf-8")
        written.append(route_path)

        # Emit page
        field_names = [f.name for f in entity.fields]
        page_yaml = emit_page(plural, domain, entity_fqn, field_names)
        page_path = output_dir / "pages" / f"{plural}.contract.yaml"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(page_yaml, encoding="utf-8")
        written.append(page_path)

    # Emit workflows
    for wf in report.workflows:
        safe_name = normalize_name(wf.name)
        data = wf.to_emitter_data()
        yaml_str = emit_workflow(safe_name, domain, data)
        path = output_dir / "workflows" / f"{safe_name}.contract.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_str, encoding="utf-8")
        written.append(path)

    return written

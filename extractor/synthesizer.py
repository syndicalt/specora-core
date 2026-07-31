# extractor/synthesizer.py
"""Pass 4: Merge all extractions into a unified AnalysisReport."""

from __future__ import annotations

from pathlib import Path

from extractor.analyzers.python_models import analyze_python_models
from extractor.analyzers.routes import analyze_routes
from extractor.analyzers.typescript_types import analyze_typescript_types
from extractor.cross_ref import cross_reference
from extractor.models import AnalysisReport, ExtractedEntity, FileRole, safe_contract_name
from extractor.scanner import ScanLimits, scan_directory


def synthesize(
    source_path: Path,
    domain: str,
    *,
    limits: ScanLimits | None = None,
) -> AnalysisReport:
    """Run the full 4-pass extraction pipeline.

    Pass 1: Scan and classify files
    Pass 2: Extract entities from model files, routes from route files
    Pass 3: Cross-reference and detect workflows
    Pass 4: Build the AnalysisReport

    Every file the pipeline declines to analyze — unreadable, unparseable, over
    the size limit, outside the scan root — lands in `report.warnings`. The
    report is a claim about a codebase, so what it could not see is part of it.
    """
    warnings: list[str] = []

    files = scan_directory(source_path, limits=limits, warnings=warnings)

    python_models = [f.path for f in files if f.role == FileRole.MODEL and f.language == "python"]
    ts_models = [f.path for f in files if f.role == FileRole.MODEL and f.language == "typescript"]
    route_files = [f.path for f in files if f.role == FileRole.ROUTE]

    unsupported = {
        f.language for f in files if f.role == FileRole.MODEL and f.language in ("sql", "prisma")
    }
    for language in sorted(unsupported):
        warnings.append(f"{language} schema files were found but there is no {language} analyzer")

    entities = analyze_python_models(python_models, source_path, warnings=warnings)
    entities.extend(analyze_typescript_types(ts_models, source_path, warnings=warnings))
    routes = analyze_routes(route_files, source_path, warnings=warnings)

    # Merging runs before cross-referencing: a model, its DTO projections, and
    # its TypeScript interface all normalize to one name, and cross-referencing
    # a pre-merge list produced one duplicate workflow (and one duplicate
    # warning) per occurrence.
    for entity in entities:
        entity.name = safe_contract_name(entity.name)
    entities = _merge_duplicates(entities)
    entities, routes, workflows = cross_reference(entities, routes, domain, warnings=warnings)

    return AnalysisReport(
        domain=domain,
        entities=entities,
        routes=routes,
        workflows=workflows,
        files_scanned=len(files),
        files_analyzed=len(python_models) + len(ts_models) + len(route_files),
        warnings=warnings,
    )


def _merge_duplicates(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Fold same-named entities together, keeping the richest description.

    A model, its Create/Update/Response projections, and its TypeScript
    interface all normalize to one name. Their fields are unioned; a field seen
    as required anywhere is required, because a projection that omits it is a
    narrower view, not a contradiction.
    """
    merged: dict[str, ExtractedEntity] = {}

    for entity in entities:
        existing = merged.get(entity.name)
        if existing is None:
            merged[entity.name] = entity
            continue

        by_name = {f.name: f for f in existing.fields}
        for field in entity.fields:
            current = by_name.get(field.name)
            if current is None:
                existing.fields.append(field)
                by_name[field.name] = field
                continue
            current.required = current.required or field.required
            current.sensitive = current.sensitive or field.sensitive
            current.description = current.description or field.description
            current.enum_values = current.enum_values or field.enum_values
            current.reference_entity = current.reference_entity or field.reference_entity
            current.reference_edge = current.reference_edge or field.reference_edge
            if not current.constraints:
                current.constraints = field.constraints

        existing.description = existing.description or entity.description
        if not existing.state_field and entity.state_field:
            existing.state_field = entity.state_field
            existing.state_values = entity.state_values

    return list(merged.values())

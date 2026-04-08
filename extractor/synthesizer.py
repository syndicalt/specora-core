# extractor/synthesizer.py
"""Pass 4: Merge all extractions into a unified AnalysisReport."""
from __future__ import annotations

from pathlib import Path

from extractor.analyzers.python_models import analyze_python_models
from extractor.analyzers.typescript_types import analyze_typescript_types
from extractor.analyzers.routes import analyze_routes
from extractor.cross_ref import cross_reference
from extractor.models import AnalysisReport, FileClassification, FileRole
from extractor.scanner import scan_directory


def synthesize(source_path: Path, domain: str) -> AnalysisReport:
    """Run the full 4-pass extraction pipeline.

    Pass 1: Scan and classify files
    Pass 2: Extract entities from model files, routes from route files
    Pass 3: Cross-reference and detect workflows
    Pass 4: Build the AnalysisReport
    """
    # Pass 1: Scan
    files = scan_directory(source_path)

    # Group by role and language
    python_models = [f.path for f in files if f.role == FileRole.MODEL and f.language == "python"]
    ts_models = [f.path for f in files if f.role == FileRole.MODEL and f.language == "typescript"]
    route_files = [f.path for f in files if f.role == FileRole.ROUTE]

    # Pass 2: Extract
    entities = []
    if python_models:
        entities.extend(analyze_python_models(python_models, source_path))
    if ts_models:
        entities.extend(analyze_typescript_types(ts_models, source_path))

    routes = []
    if route_files:
        routes = analyze_routes(route_files, source_path)

    # Pass 3: Cross-reference
    entities, routes, workflows = cross_reference(entities, routes, domain)

    # Deduplicate entities by name
    seen: dict[str, int] = {}
    unique_entities = []
    for e in entities:
        if e.name not in seen:
            seen[e.name] = len(unique_entities)
            unique_entities.append(e)
        else:
            # Merge fields from duplicate into existing
            existing = unique_entities[seen[e.name]]
            existing_field_names = {f.name for f in existing.fields}
            for f in e.fields:
                if f.name not in existing_field_names:
                    existing.fields.append(f)

    # Pass 4: Build report
    return AnalysisReport(
        domain=domain,
        entities=unique_entities,
        routes=routes,
        workflows=workflows,
        files_scanned=len(files),
        files_analyzed=len(python_models) + len(ts_models) + len(route_files),
    )

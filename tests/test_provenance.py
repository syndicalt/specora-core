from __future__ import annotations

from pathlib import Path

from forge.provenance import first_provenance_source, parse_provenance_sources
from forge.targets.base import provenance_header
from healer.analyzer.tracer import trace_to_contract


def test_provenance_header_is_parseable() -> None:
    header = provenance_header("python", "route/helpdesk/tickets", "API routes")

    assert first_provenance_source(header) == "route/helpdesk/tickets"
    assert "Specora-Source: route/helpdesk/tickets" in header
    assert "Source: route/helpdesk/tickets" in header


def test_parse_multiple_provenance_sources() -> None:
    header = provenance_header(
        "python",
        "entity/helpdesk/ticket, entity/helpdesk/customer",
        "Pydantic models",
    )

    assert parse_provenance_sources(header) == [
        "entity/helpdesk/ticket",
        "entity/helpdesk/customer",
    ]


def test_legacy_generated_from_header_still_parses() -> None:
    assert first_provenance_source("# @generated from domain/helpdesk\n") == "domain/helpdesk"


def test_tracer_reads_generated_file_source_header(tmp_path: Path) -> None:
    generated = tmp_path / "runtime" / "backend" / "routes_tickets.py"
    generated.parent.mkdir(parents=True)
    generated.write_text(
        provenance_header("python", "route/helpdesk/tickets", "API routes"),
        encoding="utf-8",
    )

    assert trace_to_contract("", {"generated_file": str(generated)}) == "route/helpdesk/tickets"


def test_tracer_reads_source_header_from_runtime_stacktrace(tmp_path: Path) -> None:
    generated = tmp_path / "runtime" / "backend" / "routes_tickets.py"
    generated.parent.mkdir(parents=True)
    generated.write_text(
        provenance_header("python", "route/helpdesk/tickets", "API routes"),
        encoding="utf-8",
    )
    stacktrace = f'File "{generated}", line 42, in create_ticket'

    assert trace_to_contract(stacktrace, {}) == "route/helpdesk/tickets"

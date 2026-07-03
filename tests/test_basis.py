"""Evidence basis artifact tests."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.basis import basis_record, build_pack_basis, read_basis, write_basis
from docpull.cli import main

from .pack_fixtures import write_context_pack


def test_basis_record_v2_is_normalized_and_stable(tmp_path: Path) -> None:
    record = basis_record(
        claim_path="answer.question",
        claim="Parallel returns cited JSON.",
        citation_ids=["S1"],
        source_urls=["https://docs.parallel.ai/api-reference/search/search"],
        excerpts=[{"citation_id": "S1", "source_url": "https://docs.parallel.ai/", "text": "cited JSON"}],
        confidence="high",
        producer="test",
        generated_at="2026-07-01T00:00:00+00:00",
    )
    output = tmp_path / "basis.ndjson"

    report = write_basis(output, [record])
    written = read_basis(output)

    assert written[0]["schema_version"] == 2
    assert written[0]["basis_id"].startswith("basis_")
    assert written[0]["evidence_state"] == "supported"
    assert report["summary"]["supported_ratio"] == 1.0
    assert (tmp_path / "basis.report.json").exists()
    assert (tmp_path / "BASIS.md").exists()


def test_build_pack_basis_empty_pack_records_insufficient_evidence(tmp_path: Path) -> None:
    pack = tmp_path / "empty"
    pack.mkdir()

    records = build_pack_basis(pack, claim_path="pack.objective", claim="Answer from empty pack")

    assert records[0]["schema_version"] == 2
    assert records[0]["evidence_state"] == "insufficient"
    assert records[0]["confidence"] == "low"


def test_pack_basis_cli_writes_report_and_markdown(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)

    assert main(["pack", "basis", str(pack), "--claim", "cited JSON results"]) == 0

    records = read_basis(pack / "basis.ndjson")
    report = json.loads((pack / "basis.report.json").read_text(encoding="utf-8"))
    assert records[0]["schema_version"] == 2
    assert report["summary"]["basis_count"] >= 1
    assert (pack / "BASIS.md").exists()

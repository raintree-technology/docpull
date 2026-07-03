"""Tests for local-first pack workflows."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from docpull import local_workflows
from docpull.cli import main
from docpull.local_workflows import LocalWorkflowError, answer_pack, audit_pack, refresh_pack
from tests.pack_fixtures import write_context_pack


def test_refresh_pack_dry_run_writes_reports(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    payload = refresh_pack(pack_dir, dry_run=True)

    assert payload["dry_run"] is True
    assert payload["summary"]["planned_url_count"] == 1
    assert (pack_dir / "refresh.report.json").exists()
    assert (pack_dir / "refresh.report.md").exists()


def test_refresh_pack_fetches_selected_urls_with_non_secret_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFetcher:
        def __init__(self, _config: Any) -> None:
            pass

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def fetch_one(self, url: str, *, save: bool) -> SimpleNamespace:
            assert save is False
            if url.endswith("/error"):
                return SimpleNamespace(error="network failed", should_skip=False)
            if url.endswith("/skip"):
                return SimpleNamespace(error=None, should_skip=True, skip_reason="robots", skip_code="ROBOTS")
            if url.endswith("/empty"):
                return SimpleNamespace(
                    error=None,
                    should_skip=False,
                    markdown="",
                    title="Empty",
                    metadata={},
                    extraction_info={},
                    source_type="test",
                )
            return SimpleNamespace(
                error=None,
                should_skip=False,
                markdown="Fresh cited JSON content.",
                title="Fresh",
                metadata={"provider": "fake"},
                extraction_info={"method": "fake"},
                source_type="test",
            )

    records = [
        {
            "document_id": "doc_ok",
            "url": "https://docs.parallel.ai/ok",
            "title": "OK",
            "content": "Old content",
            "content_hash": "old_ok",
            "source_type": "parallel_extract",
        },
        {
            "document_id": "doc_error",
            "url": "https://docs.parallel.ai/error",
            "title": "Error",
            "content": "Old error",
            "content_hash": "old_error",
            "source_type": "parallel_extract",
        },
        {
            "document_id": "doc_skip",
            "url": "https://docs.parallel.ai/skip",
            "title": "Skip",
            "content": "Old skip",
            "content_hash": "old_skip",
            "source_type": "parallel_extract",
        },
        {
            "document_id": "doc_empty",
            "url": "https://docs.parallel.ai/empty",
            "title": "Empty",
            "content": "Old empty",
            "content_hash": "old_empty",
            "source_type": "parallel_extract",
        },
    ]
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir, records=records)
    monkeypatch.setattr(local_workflows, "Fetcher", FakeFetcher)

    payload = refresh_pack(pack_dir, output_dir=tmp_path / "refreshed", changed_only=True)

    assert payload["dry_run"] is False
    assert payload["summary"]["fetched_count"] == 1
    assert payload["summary"]["failed_count"] == 1
    assert payload["summary"]["skipped_count"] == 2
    assert (tmp_path / "refreshed" / "local.pack.json").exists()
    assert "network failed" in (pack_dir / "refresh.report.md").read_text(encoding="utf-8")


def test_refresh_cli_reports_success_and_user_errors(tmp_path: Path, capsys) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    assert local_workflows.run_refresh_cli([str(pack_dir), "--dry-run"]) == 0
    assert "Refresh dry run" in capsys.readouterr().out

    empty_pack = tmp_path / "empty"
    empty_pack.mkdir()
    (empty_pack / "documents.ndjson").write_text("", encoding="utf-8")

    assert local_workflows.run_refresh_cli([str(empty_pack), "--dry-run"]) == 1
    assert "Cannot refresh an empty pack" in capsys.readouterr().out


def test_pack_audit_writes_json_and_markdown(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    payload = audit_pack(pack_dir, fail_under=0.5)

    assert payload["score"] >= 50
    assert payload["passed"] is True
    assert (pack_dir / "pack.audit.json").exists()
    assert (pack_dir / "PACK_AUDIT.md").exists()
    assert "citation_coverage" in payload["dimensions"]


def test_pack_audit_cli(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    assert main(["pack", "audit", str(pack_dir), "--fail-under", "0.5"]) == 0

    payload = json.loads((pack_dir / "pack.audit.json").read_text(encoding="utf-8"))
    assert payload["grade"] in {"excellent", "good", "needs_review", "poor"}


def test_pack_audit_fail_under_raises_after_writing_reports(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    with pytest.raises(LocalWorkflowError, match="below fail_under"):
        audit_pack(pack_dir, fail_under=1.0)

    assert (pack_dir / "pack.audit.json").exists()
    assert (pack_dir / "PACK_AUDIT.md").exists()


def test_answer_pack_uses_local_evidence(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    payload = answer_pack(pack_dir, "What does Parallel Search return?")

    assert payload["answer"]["status"] == "answered_from_local_pack"
    assert "cited JSON" in payload["answer"]["text"]
    assert (pack_dir / "answer.result.json").exists()
    assert (pack_dir / "answer.report.md").exists()


def test_answer_pack_cli_returns_two_for_missing_evidence(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    assert local_workflows.run_answer_cli([str(pack_dir), "unrelated zyzzyva query"]) == 2

    payload = json.loads((pack_dir / "answer.result.json").read_text(encoding="utf-8"))
    assert payload["answer"]["status"] == "insufficient_evidence"


def test_answer_pack_rejects_empty_questions_and_bad_limits(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    with pytest.raises(LocalWorkflowError, match="question"):
        answer_pack(pack_dir, " ")
    with pytest.raises(LocalWorkflowError, match="limit"):
        answer_pack(pack_dir, "Question?", limit=0)

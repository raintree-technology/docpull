"""Tests for local-first pack workflows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from docpull import local_workflows
from docpull.cli import main
from docpull.local_workflows import LocalWorkflowError, answer_pack, audit_pack, refresh_pack
from tests.pack_fixtures import write_context_pack

AUDIT_NOW = datetime(2026, 3, 10, tzinfo=timezone.utc)


def _aged_record(suffix: str) -> dict[str, Any]:
    return {
        "document_id": f"doc_{suffix}",
        "url": f"https://docs.parallel.ai/{suffix}",
        "title": suffix,
        "content": f"Parallel Search API {suffix} content.",
        "content_hash": f"hash_{suffix}",
        "source_type": "parallel_extract",
    }


def _set_manifest_fetched_at(pack_dir: Path, fetched_by_url: dict[str, str]) -> None:
    manifest_path = pack_dir / "corpus.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["records"]:
        value = fetched_by_url.get(record["url"])
        if value is not None:
            record["fetched_at"] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


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


def test_pack_audit_without_max_age_reports_stale_sources_not_evaluated(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    payload = audit_pack(pack_dir)

    assert payload["stale_sources"] == {
        "evaluated": False,
        "max_age_days": None,
        "stale": [],
        "stale_count": 0,
        "unknown_age_count": 0,
        "freshest_fetched_at": None,
        "oldest_fetched_at": None,
    }
    assert payload["summary"]["stale_source_count"] == 0


def test_pack_audit_flags_stale_sources_by_age(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = tmp_path / "pack"
    records = [_aged_record(suffix) for suffix in ("old", "fresh", "invalid", "missing")]
    write_context_pack(pack_dir, records=records)
    _set_manifest_fetched_at(
        pack_dir,
        {
            "https://docs.parallel.ai/old": "2026-01-01T00:00:00+00:00",
            "https://docs.parallel.ai/fresh": "2026-03-09T00:00:00+00:00",
            "https://docs.parallel.ai/invalid": "not-a-timestamp",
        },
    )
    monkeypatch.setattr(local_workflows, "_audit_now", lambda: AUDIT_NOW)

    baseline = audit_pack(pack_dir)
    payload = audit_pack(pack_dir, max_age_days=30.0)

    stale_sources = payload["stale_sources"]
    assert stale_sources["evaluated"] is True
    assert stale_sources["max_age_days"] == 30.0
    assert stale_sources["stale_count"] == 1
    assert stale_sources["unknown_age_count"] == 2
    assert stale_sources["stale"] == [
        {
            "url": "https://docs.parallel.ai/old",
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "age_days": 68.0,
        }
    ]
    assert stale_sources["freshest_fetched_at"] == "2026-03-09T00:00:00+00:00"
    assert stale_sources["oldest_fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert payload["summary"]["stale_source_count"] == 1
    assert payload["score"] == baseline["score"] - 5
    issue_codes = {issue["code"] for issue in payload["issues"]}
    assert "stale_sources" in issue_codes
    markdown = (pack_dir / "PACK_AUDIT.md").read_text(encoding="utf-8")
    assert "Stale Sources" in markdown
    assert f"docpull refresh {pack_dir} --changed-only" in markdown


def test_pack_audit_uses_source_policy_max_age_as_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir, records=[_aged_record("old")])
    _set_manifest_fetched_at(
        pack_dir,
        {"https://docs.parallel.ai/old": "2026-01-01T00:00:00+00:00"},
    )
    (pack_dir / "source_policy.json").write_text(
        json.dumps({"freshness": {"max_age_seconds": 30 * 86400}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(local_workflows, "_audit_now", lambda: AUDIT_NOW)

    payload = audit_pack(pack_dir)

    stale_sources = payload["stale_sources"]
    assert stale_sources["evaluated"] is True
    assert stale_sources["max_age_days"] == 30.0
    assert stale_sources["stale_count"] == 1

    explicit = audit_pack(pack_dir, max_age_days=365.0)
    assert explicit["stale_sources"]["max_age_days"] == 365.0
    assert explicit["stale_sources"]["stale_count"] == 0


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

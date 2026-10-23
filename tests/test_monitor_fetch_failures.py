"""Regression: a URL that failed to fetch must never be reported as removed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from docpull import local_workflows
from docpull.local_workflows import refresh_pack
from docpull.monitor import init_monitor, run_monitor_once
from tests.pack_fixtures import write_context_pack

OK_URL = "https://docs.example.com/ok"
ERROR_URL = "https://docs.example.com/error"
SKIP_URL = "https://docs.example.com/skip"


class _Fetcher:
    def __init__(self, _config: Any) -> None:
        pass

    async def __aenter__(self) -> _Fetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def fetch_one(self, url: str, *, save: bool) -> SimpleNamespace:
        if url == ERROR_URL:
            return SimpleNamespace(error="HTTP 503", should_skip=False)
        if url == SKIP_URL:
            return SimpleNamespace(error=None, should_skip=True, skip_reason="robots", skip_code="ROBOTS")
        return SimpleNamespace(
            error=None,
            should_skip=False,
            markdown="Fresh content.",
            title="Fresh",
            metadata={},
            extraction_info={},
            source_type="test",
        )


def _record(doc_id: str, url: str) -> dict[str, Any]:
    return {
        "document_id": doc_id,
        "url": url,
        "title": doc_id,
        "content": f"old {doc_id}",
        "content_hash": f"hash_{doc_id}",
        "source_type": "test",
    }


@pytest.fixture
def pack_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "pack"
    write_context_pack(
        path,
        records=[_record("ok", OK_URL), _record("error", ERROR_URL), _record("skip", SKIP_URL)],
    )
    monkeypatch.setattr(local_workflows, "Fetcher", _Fetcher)
    return path


def test_refresh_does_not_report_failed_fetch_as_removed(pack_dir: Path, tmp_path: Path) -> None:
    payload = refresh_pack(pack_dir, output_dir=tmp_path / "refreshed")

    assert payload["summary"]["failed_count"] == 1
    assert payload["diff"]["removed_urls"] == []
    assert [e for e in payload["diff"]["change_events"] if e["url"] in {ERROR_URL, SKIP_URL}] == []


def test_monitor_does_not_classify_failed_fetch_as_disappeared(pack_dir: Path, tmp_path: Path) -> None:
    state_dir = tmp_path / "monitors"
    init_monitor("docs", pack_dir, state_dir=state_dir)

    payload = run_monitor_once("docs", state_dir=state_dir)

    assert payload["summary"]["removed_count"] == 0
    types = {(c["url"], c["change_type"]) for c in payload["change_classes"]["classifications"]}
    assert (ERROR_URL, "source_disappeared") not in types
    assert (SKIP_URL, "source_disappeared") not in types
    project_diff = Path(payload["run_dir"], payload["artifacts"]["project_diff"]).read_text()
    assert ERROR_URL not in project_diff


def test_refresh_still_reports_404_as_removed(pack_dir: Path, tmp_path: Path, monkeypatch) -> None:
    class _Gone(_Fetcher):
        async def fetch_one(self, url: str, *, save: bool) -> SimpleNamespace:
            if url == SKIP_URL:
                return SimpleNamespace(
                    error=None, should_skip=True, skip_reason="HTTP 404", skip_code="HTTP_ERROR"
                )
            return await super().fetch_one(url, save=save)

    monkeypatch.setattr(local_workflows, "Fetcher", _Gone)
    payload = refresh_pack(pack_dir, output_dir=tmp_path / "refreshed")

    assert payload["diff"]["removed_urls"] == [SKIP_URL]
    assert ERROR_URL not in payload["diff"]["removed_urls"]

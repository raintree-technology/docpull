"""Tests for the optional extractor ensemble."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull.conversion.ensemble import ExtractorEnsemble
from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.convert import ConvertStep


def _html(body: str) -> bytes:
    return f"<html><body>{body}</body></html>".encode()


def test_extractor_ensemble_uses_generic_without_trafilatura() -> None:
    ensemble = ExtractorEnsemble(include_trafilatura=False)

    markdown, details = ensemble.extract(
        _html("<main><h1>Docs</h1><p>Install and configure the API client.</p></main>"),
        "https://docs.example.com/start",
    )

    assert "Install and configure" in markdown
    assert details["selected"] == "generic"
    assert details["candidate_count"] == 1


def test_extractor_ensemble_can_select_optional_trafilatura(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_extract(*_args: object, **_kwargs: object) -> str:
        return "# Article\n\n" + ("Useful reference content. " * 120)

    monkeypatch.setitem(sys.modules, "trafilatura", SimpleNamespace(extract=fake_extract))
    ensemble = ExtractorEnsemble(include_trafilatura=True)

    markdown, details = ensemble.extract(
        _html("<main><p>Short fallback.</p></main>"),
        "https://docs.example.com/article",
    )

    assert markdown.startswith("# Article")
    assert details["selected"] == "trafilatura"
    assert {candidate["name"] for candidate in details["candidates"]} == {"generic", "trafilatura"}


@pytest.mark.asyncio
async def test_convert_step_records_ensemble_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "trafilatura", SimpleNamespace(extract=lambda *_args, **_kwargs: None))
    step = ConvertStep(add_frontmatter=False, use_ensemble=True)
    ctx = PageContext(
        url="https://docs.example.com/page",
        output_path=Path("/tmp/page.md"),
        html=_html("<main><h1>Guide</h1><p>Operational setup notes for the local API.</p></main>"),
    )

    result = await step.execute(ctx)

    assert result.markdown is not None
    assert result.extraction_info["method"] == "ensemble"
    assert result.extraction_info["ensemble"]["selected"] == "generic"

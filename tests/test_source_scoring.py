"""Source scoring regression tests."""

from __future__ import annotations

from docpull.source_scoring import score_source


def test_source_scoring_prefers_guides_over_locale_homepages() -> None:
    expected_domains = ["fastapi.tiangolo.com"]

    tutorial = score_source(
        url="https://fastapi.tiangolo.com/tutorial/",
        title="Tutorial - User Guide",
        expected_domains=expected_domains,
    )
    locale = score_source(
        url="https://fastapi.tiangolo.com/fr/",
        title="FastAPI - FastAPI",
        expected_domains=expected_domains,
    )

    assert tutorial["score"] > locale["score"]
    assert "docs_path" in tutorial["reasons"]
    assert "locale_home_path" in locale["reasons"]


def test_source_scoring_deprioritizes_newsletter_paths() -> None:
    homepage = score_source(
        url="https://fastapi.tiangolo.com/",
        title="FastAPI - FastAPI",
        expected_domains=["fastapi.tiangolo.com"],
    )
    newsletter = score_source(
        url="https://fastapi.tiangolo.com/newsletter/",
        title="FastAPI and friends newsletter - FastAPI",
        expected_domains=["fastapi.tiangolo.com"],
    )

    assert homepage["score"] > newsletter["score"]
    assert "lower_priority_path" in newsletter["reasons"]

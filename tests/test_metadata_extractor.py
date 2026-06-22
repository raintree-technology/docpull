"""Tests for rich metadata extraction helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from docpull.metadata_extractor import RichMetadataExtractor


def test_extract_merges_opengraph_jsonld_and_microdata(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_extract(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["syntaxes"] == ["opengraph", "json-ld", "microdata"]
        return {
            "opengraph": [
                {
                    "properties": [
                        {"og:title": ["OG Title"]},
                        {"og:description": "OG Description"},
                        {"og:image": "https://example.com/og.png"},
                        {"og:type": "article"},
                        {"og:site_name": "Example Docs"},
                        {"og:url": "https://example.com/canonical"},
                        {"article:author": "OG Author"},
                        {"article:published_time": "2026-01-01T00:00:00Z"},
                        {"article:modified_time": "2026-01-02T00:00:00Z"},
                        {"article:section": "Guides"},
                        {"article:tag": ["api", "docs"]},
                    ]
                }
            ],
            "json-ld": [
                {
                    "headline": "JSON-LD title should not overwrite",
                    "keywords": "python, docs, agents",
                }
            ],
            "microdata": [{"properties": {"headline": "Microdata title should not overwrite"}}],
        }

    monkeypatch.setitem(__import__("sys").modules, "extruct", SimpleNamespace(extract=fake_extract))

    metadata = RichMetadataExtractor().extract("<html></html>", "https://example.com/page")

    assert metadata == {
        "url": "https://example.com/page",
        "title": "Microdata title should not overwrite",
        "description": "OG Description",
        "image": "https://example.com/og.png",
        "type": "article",
        "site_name": "Example Docs",
        "canonical_url": "https://example.com/canonical",
        "author": "OG Author",
        "published_time": "2026-01-01T00:00:00Z",
        "modified_time": "2026-01-02T00:00:00Z",
        "section": "Guides",
        "tags": ["api"],
        "keywords": ["python", "docs", "agents"],
    }


def test_extract_returns_minimal_metadata_when_extruct_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real_import = __import__("builtins").__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "extruct":
            raise ImportError("missing extruct")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(__import__("builtins"), "__import__", fake_import)

    with caplog.at_level("WARNING", logger="docpull.metadata_extractor"):
        metadata = RichMetadataExtractor().extract("<html></html>", "https://example.com")

    assert metadata == {"url": "https://example.com", "title": None}
    assert "rich metadata extraction disabled" in caplog.text


def test_extract_swallows_malformed_extruct_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_extract(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("bad structured data")

    monkeypatch.setitem(__import__("sys").modules, "extruct", SimpleNamespace(extract=fake_extract))

    with caplog.at_level("DEBUG", logger="docpull.metadata_extractor"):
        metadata = RichMetadataExtractor().extract("<html></html>", "https://example.com")

    assert metadata == {"url": "https://example.com", "title": None}
    assert "Could not extract rich metadata" in caplog.text


def test_jsonld_microdata_safe_string_and_fallback_paths() -> None:
    extractor = RichMetadataExtractor()

    jsonld = extractor._extract_jsonld(
        [
            "not a dict",  # type: ignore[list-item]
            {
                "headline": [" JSON Title "],
                "description": None,
                "author": {"name": " Ada "},
                "datePublished": "2026-01-01",
                "dateModified": "2026-01-02",
                "keywords": ["alpha", 123, ""],
                "image": {"url": " https://example.com/image.png "},
            },
            {
                "headline": "Ignored later title",
                "author": "Ignored later author",
                "image": "Ignored later image",
            },
        ]
    )
    assert jsonld == {
        "title": "JSON Title",
        "description": "",
        "author": "Ada",
        "published_time": "2026-01-01",
        "modified_time": "2026-01-02",
        "keywords": ["alpha", "123"],
        "image": "https://example.com/image.png",
    }

    microdata = extractor._extract_microdata(
        [
            "not a dict",  # type: ignore[list-item]
            {},
            {"properties": {"headline": " Micro Title ", "description": " Desc ", "author": " Grace "}},
            {"properties": {"author": {"properties": {"name": "Ignored later author"}}}},
        ]
    )
    assert microdata == {"title": "Micro Title", "description": "Desc", "author": "Grace"}

    assert extractor._safe_string(None) == ""
    assert extractor._safe_string([]) == ""
    assert extractor._safe_string((" first ", "second")) == "first"
    assert extractor._safe_string(42) == "42"

    assert extractor.merge_with_fallback({"url": "https://example.com", "title": None}, "Fallback") == {
        "url": "https://example.com",
        "title": "Fallback",
    }

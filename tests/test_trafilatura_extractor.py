"""Tests for the optional trafilatura extractor."""

from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from docpull.conversion.trafilatura_extractor import TrafilaturaExtractor


def test_trafilatura_extractor_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "trafilatura":
            raise ImportError("missing trafilatura")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match=r"docpull\[trafilatura\]"):
        TrafilaturaExtractor()


def test_trafilatura_extractor_returns_markdown_and_passes_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_extract(text: str, **kwargs: object) -> str:
        calls.append({"text": text, **kwargs})
        return "  # Extracted\n"

    monkeypatch.setitem(
        __import__("sys").modules,
        "trafilatura",
        SimpleNamespace(extract=fake_extract),
    )

    extractor = TrafilaturaExtractor(include_links=False, include_tables=False)
    result = extractor.extract(b"<html><body>hello</body></html>", "https://example.com")

    assert result == "# Extracted\n"
    assert calls == [
        {
            "text": "<html><body>hello</body></html>",
            "url": "https://example.com",
            "output_format": "markdown",
            "include_links": False,
            "include_tables": False,
            "include_comments": False,
            "include_formatting": True,
            "favor_precision": True,
        }
    ]


def test_trafilatura_extractor_returns_empty_string_for_no_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        __import__("sys").modules,
        "trafilatura",
        SimpleNamespace(extract=lambda *_args, **_kwargs: None),
    )

    assert TrafilaturaExtractor().extract(b"<html></html>", "https://example.com") == ""


def test_trafilatura_extractor_latin1_fallback_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Utf8RejectingBytes(bytes):
        def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
            if encoding == "utf-8":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "simulated")
            return "latin-1 body"

    def fake_extract(text: str, **_kwargs: object) -> str:
        calls.append(text)
        return "body"

    monkeypatch.setitem(
        __import__("sys").modules,
        "trafilatura",
        SimpleNamespace(extract=fake_extract),
    )

    assert TrafilaturaExtractor().extract(Utf8RejectingBytes(b"\xff"), "https://example.com") == "body\n"
    assert calls == ["latin-1 body"]

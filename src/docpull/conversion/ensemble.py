"""Extractor ensemble for choosing the best local Markdown candidate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .extractor import MainContentExtractor
from .markdown import HtmlToMarkdown

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{1,}")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S+", re.M)
_LINK_RE = re.compile(r"\[[^\]]+]\([^)]+\)")
_BOILERPLATE_RE = re.compile(
    r"\b(cookie|privacy policy|terms of use|subscribe|newsletter|sign in|log in|loading)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractionCandidate:
    """One Markdown candidate produced by a content extraction route."""

    name: str
    markdown: str
    score: float
    metadata: dict[str, Any]


class ExtractorEnsemble:
    """Run available local extractors and choose the strongest Markdown output."""

    def __init__(
        self,
        *,
        extractor: MainContentExtractor | None = None,
        converter: HtmlToMarkdown | None = None,
        include_trafilatura: bool = True,
    ) -> None:
        self._extractor = extractor or MainContentExtractor()
        self._converter = converter or HtmlToMarkdown()
        self._include_trafilatura = include_trafilatura
        self._trafilatura_error: str | None = None
        self._trafilatura: Any | None = None
        if include_trafilatura:
            try:
                from .trafilatura_extractor import TrafilaturaExtractor

                self._trafilatura = TrafilaturaExtractor()
            except ImportError as err:
                self._trafilatura_error = str(err)

    def extract(self, html: bytes, url: str) -> tuple[str, dict[str, Any]]:
        """Return the selected Markdown plus ensemble diagnostics."""
        candidates = self.candidates(html, url)
        if not candidates:
            return "", self._diagnostics([], selected=None)
        selected = max(candidates, key=lambda item: (item.score, _candidate_tiebreaker(item.name)))
        return selected.markdown, self._diagnostics(candidates, selected=selected)

    def candidates(self, html: bytes, url: str) -> list[ExtractionCandidate]:
        """Return all successful Markdown candidates."""
        candidates: list[ExtractionCandidate] = []
        generic = self._generic_candidate(html, url)
        if generic is not None:
            candidates.append(generic)
        trafilatura = self._trafilatura_candidate(html, url)
        if trafilatura is not None:
            candidates.append(trafilatura)
        return candidates

    def _generic_candidate(self, html: bytes, url: str) -> ExtractionCandidate | None:
        extracted_html = self._extractor.extract(html, url)
        if not extracted_html.strip():
            return None
        markdown = self._converter.convert(extracted_html, url)
        if not markdown.strip():
            return None
        return _candidate("generic", markdown, {"extracted_html_bytes": len(extracted_html.encode("utf-8"))})

    def _trafilatura_candidate(self, html: bytes, url: str) -> ExtractionCandidate | None:
        if self._trafilatura is None:
            return None
        markdown = self._trafilatura.extract(html, url)
        if not markdown.strip():
            return None
        return _candidate("trafilatura", markdown, {})

    def _diagnostics(
        self,
        candidates: list[ExtractionCandidate],
        *,
        selected: ExtractionCandidate | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "selected": selected.name if selected else None,
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "name": candidate.name,
                    "score": candidate.score,
                    **candidate.metadata,
                }
                for candidate in candidates
            ],
        }
        if self._include_trafilatura and self._trafilatura is None and self._trafilatura_error:
            payload["trafilatura"] = {
                "available": False,
                "reason": "missing_optional_dependency",
            }
        return payload


def _candidate(name: str, markdown: str, metadata: dict[str, Any]) -> ExtractionCandidate:
    metrics = _quality_metrics(markdown)
    return ExtractionCandidate(
        name=name,
        markdown=markdown,
        score=_score_metrics(metrics),
        metadata={**metadata, **metrics},
    )


def _quality_metrics(markdown: str) -> dict[str, Any]:
    words = _WORD_RE.findall(markdown)
    unique_words = {word.lower() for word in words}
    line_count = len([line for line in markdown.splitlines() if line.strip()])
    boilerplate_count = len(_BOILERPLATE_RE.findall(markdown))
    return {
        "char_count": len(markdown),
        "word_count": len(words),
        "unique_word_count": len(unique_words),
        "heading_count": len(_HEADING_RE.findall(markdown)),
        "link_count": len(_LINK_RE.findall(markdown)),
        "code_fence_count": markdown.count("```") // 2,
        "line_count": line_count,
        "boilerplate_count": boilerplate_count,
    }


def _score_metrics(metrics: dict[str, Any]) -> float:
    word_count = _safe_int(metrics.get("word_count"))
    unique_word_count = _safe_int(metrics.get("unique_word_count"))
    heading_count = _safe_int(metrics.get("heading_count"))
    link_count = _safe_int(metrics.get("link_count"))
    code_fence_count = _safe_int(metrics.get("code_fence_count"))
    boilerplate_count = _safe_int(metrics.get("boilerplate_count"))

    score = 0.0
    score += min(word_count / 700.0, 0.42)
    score += min(unique_word_count / 350.0, 0.20)
    score += min(heading_count * 0.04, 0.16)
    score += min(link_count * 0.015, 0.08)
    score += min(code_fence_count * 0.03, 0.06)
    if word_count < 30:
        score -= 0.25
    if boilerplate_count:
        score -= min(boilerplate_count * 0.035, 0.18)
    return max(0.0, round(min(score, 1.0), 4))


def _safe_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    return 0


def _candidate_tiebreaker(name: str) -> int:
    if name == "trafilatura":
        return 2
    if name == "generic":
        return 1
    return 0


__all__ = ["ExtractionCandidate", "ExtractorEnsemble"]

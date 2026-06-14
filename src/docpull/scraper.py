"""Scraper-facing API built on top of the core Fetcher.

This module intentionally does not introduce a second crawling engine. It gives
users scraper-native names for docpull's existing browser-free, SSRF-hardened
fetch/convert pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .core.fetcher import Fetcher
from .models.config import (
    DocpullConfig,
    ProfileName,
)
from .models.events import FetchEvent, FetchStats
from .pipeline.base import PageContext

ExtractorName = Literal["default", "trafilatura"]
OutputFormat = Literal["markdown", "json", "ndjson", "sqlite", "okf"]


@dataclass(frozen=True)
class ScrapeResult:
    """In-memory result for a single scraped URL."""

    url: str
    markdown: str
    title: str | None = None
    source_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    extraction: dict[str, Any] = field(default_factory=dict)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None

    @property
    def text(self) -> str:
        """Alias for callers that expect scraper results to expose text."""
        return self.markdown

    @classmethod
    def from_context(cls, ctx: PageContext) -> ScrapeResult:
        chunks: list[dict[str, Any]] = []
        for chunk in ctx.chunks:
            chunks.append(
                {
                    "index": getattr(chunk, "index", 0),
                    "heading": getattr(chunk, "heading", None),
                    "token_count": getattr(chunk, "token_count", None),
                    "text": getattr(chunk, "text", ""),
                }
            )
        return cls(
            url=ctx.url,
            markdown=ctx.markdown or "",
            title=ctx.title,
            source_type=ctx.source_type,
            metadata=dict(ctx.metadata or {}),
            extraction=dict(ctx.extraction_info or {}),
            chunks=chunks,
            error=ctx.error,
            skipped=ctx.should_skip,
            skip_reason=ctx.skip_reason,
        )


@dataclass(frozen=True)
class ScrapeRunResult:
    """Summary for a site scrape that writes output artifacts to disk."""

    start_url: str
    output_dir: Path
    output_format: str
    stats: FetchStats

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / "corpus.manifest.json"


class Scraper:
    """Browser-free web scraper facade over :class:`docpull.Fetcher`."""

    def __init__(
        self,
        *,
        extractor: ExtractorName | None = None,
        strict_js_required: bool | None = None,
        **config_defaults: Any,
    ) -> None:
        self._extractor = extractor
        self._strict_js_required = strict_js_required
        self._config_defaults = dict(config_defaults)

    async def scrape_one(
        self,
        url: str,
        *,
        max_tokens_per_file: int | None = None,
        **config_kwargs: Any,
    ) -> ScrapeResult:
        """Fetch and convert one URL without crawling or writing files."""
        merged_kwargs = _merge_config_kwargs(self._config_defaults, config_kwargs)
        return await scrape_one(
            url,
            extractor=self._extractor,
            strict_js_required=self._strict_js_required,
            max_tokens_per_file=max_tokens_per_file,
            **merged_kwargs,
        )

    async def scrape_site(
        self,
        url: str,
        *,
        output_dir: Path = Path("./docs"),
        output_format: OutputFormat | None = None,
        profile: ProfileName = ProfileName.RAG,
        max_pages: int | None = None,
        max_depth: int | None = None,
        **config_kwargs: Any,
    ) -> ScrapeRunResult:
        """Crawl a site and write artifacts using docpull's output sinks."""
        merged_kwargs = _merge_config_kwargs(self._config_defaults, config_kwargs)
        return await scrape_site(
            url,
            output_dir=output_dir,
            output_format=output_format,
            profile=profile,
            max_pages=max_pages,
            max_depth=max_depth,
            extractor=self._extractor,
            strict_js_required=self._strict_js_required,
            **merged_kwargs,
        )

    async def iter_scrape(
        self,
        url: str,
        *,
        output_dir: Path = Path("./docs"),
        output_format: OutputFormat | None = None,
        profile: ProfileName = ProfileName.RAG,
        max_pages: int | None = None,
        max_depth: int | None = None,
        **config_kwargs: Any,
    ) -> AsyncIterator[FetchEvent]:
        """Yield streaming events for a crawl."""
        merged_kwargs = _merge_config_kwargs(self._config_defaults, config_kwargs)
        config = _site_config(
            url,
            output_dir=output_dir,
            output_format=output_format,
            profile=profile,
            max_pages=max_pages,
            max_depth=max_depth,
            extractor=self._extractor,
            strict_js_required=self._strict_js_required,
            config_kwargs=merged_kwargs,
        )
        async with Fetcher(config) as fetcher:
            async for event in fetcher.run():
                yield event


async def scrape_one(
    url: str,
    *,
    extractor: ExtractorName | None = None,
    strict_js_required: bool | None = None,
    max_tokens_per_file: int | None = None,
    **config_kwargs: Any,
) -> ScrapeResult:
    """Fetch one server-rendered/static URL and return Markdown in memory."""
    output_data = _section_dict(config_kwargs.pop("output", None))
    if max_tokens_per_file is not None:
        output_data["max_tokens_per_file"] = max_tokens_per_file
    content_filter_data = _section_dict(config_kwargs.pop("content_filter", None))
    if extractor is not None:
        content_filter_data["extractor"] = extractor
    if strict_js_required is not None:
        content_filter_data["strict_js_required"] = strict_js_required
    config_kwargs.pop("url", None)
    profile = config_kwargs.pop("profile", ProfileName.CUSTOM)
    if output_data:
        config_kwargs["output"] = output_data
    if content_filter_data:
        config_kwargs["content_filter"] = content_filter_data
    config = DocpullConfig(
        url=url,
        profile=profile,
        **config_kwargs,
    )
    async with Fetcher(config) as fetcher:
        ctx = await fetcher.fetch_one(url, save=False)
    return ScrapeResult.from_context(ctx)


def scrape_one_blocking(
    url: str,
    *,
    extractor: ExtractorName | None = None,
    strict_js_required: bool | None = None,
    max_tokens_per_file: int | None = None,
    **config_kwargs: Any,
) -> ScrapeResult:
    """Synchronous wrapper for :func:`scrape_one`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            scrape_one(
                url,
                extractor=extractor,
                strict_js_required=strict_js_required,
                max_tokens_per_file=max_tokens_per_file,
                **config_kwargs,
            )
        )
    raise RuntimeError("scrape_one_blocking() called from async context. Use scrape_one() instead.")


async def scrape_site(
    url: str,
    *,
    output_dir: Path = Path("./docs"),
    output_format: OutputFormat | None = None,
    profile: ProfileName = ProfileName.RAG,
    max_pages: int | None = None,
    max_depth: int | None = None,
    extractor: ExtractorName | None = None,
    strict_js_required: bool | None = None,
    **config_kwargs: Any,
) -> ScrapeRunResult:
    """Crawl a server-rendered/static site and write output artifacts."""
    config = _site_config(
        url,
        output_dir=output_dir,
        output_format=output_format,
        profile=profile,
        max_pages=max_pages,
        max_depth=max_depth,
        extractor=extractor,
        strict_js_required=strict_js_required,
        config_kwargs=config_kwargs,
    )
    async with Fetcher(config) as fetcher:
        async for _event in fetcher.run():
            pass
        stats = fetcher.stats
        applied_output_dir = fetcher.config.output.directory
        applied_output_format = fetcher.config.output.format
    return ScrapeRunResult(
        start_url=url,
        output_dir=applied_output_dir,
        output_format=applied_output_format,
        stats=stats,
    )


def _site_config(
    url: str,
    *,
    output_dir: Path,
    output_format: OutputFormat | None,
    profile: ProfileName,
    max_pages: int | None,
    max_depth: int | None,
    extractor: ExtractorName | None,
    strict_js_required: bool | None,
    config_kwargs: dict[str, Any] | None = None,
) -> DocpullConfig:
    config_data = dict(config_kwargs or {})
    config_data.pop("url", None)
    config_data.pop("profile", None)

    crawl_data = _section_dict(config_data.pop("crawl", None))
    if max_pages is not None:
        crawl_data["max_pages"] = max_pages
    if max_depth is not None:
        crawl_data["max_depth"] = max_depth

    output_data = _section_dict(config_data.pop("output", None))
    output_data["directory"] = output_dir
    if output_format is not None:
        output_data["format"] = output_format

    content_filter_data = _section_dict(config_data.pop("content_filter", None))
    if extractor is not None:
        content_filter_data["extractor"] = extractor
    if strict_js_required is not None:
        content_filter_data["strict_js_required"] = strict_js_required

    if crawl_data:
        config_data["crawl"] = crawl_data
    if output_data:
        config_data["output"] = output_data
    if content_filter_data:
        config_data["content_filter"] = content_filter_data

    return DocpullConfig(
        url=url,
        profile=profile,
        **config_data,
    )


def _section_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped
    raise TypeError(f"Expected a config section mapping, got {type(value).__name__}")


def _merge_config_kwargs(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in overrides.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
            and key in {"auth", "cache", "content_filter", "crawl", "network", "output", "performance"}
        ):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


__all__ = [
    "ScrapeResult",
    "ScrapeRunResult",
    "Scraper",
    "scrape_one",
    "scrape_one_blocking",
    "scrape_site",
]

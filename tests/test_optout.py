"""AI/TDM opt-out enforcement: parsers, decisions, pipeline, and CLI flags.

Pipeline tests run against a local aiohttp server (no external network),
mirroring the pattern in tests/test_cache_conditional_get.py.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web

from docpull.models.events import EventType, SkipReason
from docpull.pipeline.base import PageContext
from docpull.pipeline.steps import ConvertStep, FetchStep
from docpull.security.optout import (
    OptOutDecision,
    evaluate_optout,
    parse_robots_meta,
    parse_x_robots_tag,
)

PLAIN_HTML = b"""<!doctype html><html><head><title>Plain</title></head>
<body><article><h1>Plain page</h1><p>Nothing restricts this content.</p></article></body></html>"""

META_NOAI_HTML = b"""<!doctype html><html><head><title>Blocked</title>
<meta name="robots" content="noai"></head>
<body><article><h1>Blocked page</h1><p>This page opted out of AI reuse.</p></article></body></html>"""

META_NOINDEX_HTML = b"""<!doctype html><html><head><title>Noindex</title>
<meta name="robots" content="noindex"></head>
<body><article><h1>Noindex page</h1><p>Excluded from search, not from reuse.</p></article></body></html>"""


class TestParseXRobotsTag:
    def test_multiple_directives(self):
        assert parse_x_robots_tag("noai, noimageai") == {"noai", "noimageai"}

    def test_space_separated_directives(self):
        assert parse_x_robots_tag("noai noimageai") == {"noai", "noimageai"}

    def test_docpull_scoped_directive_applies(self):
        assert parse_x_robots_tag("docpull: noai") == {"noai"}

    def test_other_agent_scoped_directive_ignored(self):
        assert parse_x_robots_tag("otherbot: noai") == set()

    def test_scope_persists_across_commas(self):
        # Both directives belong to otherbot, so neither applies to docpull.
        assert parse_x_robots_tag("otherbot: noindex, nofollow") == set()
        # A later docpull scope re-enables collection.
        assert parse_x_robots_tag("otherbot: noindex, docpull: noai") == {"noai"}

    def test_case_insensitive(self):
        assert parse_x_robots_tag("NoAI") == {"noai"}
        assert parse_x_robots_tag("DOCPULL: NOAI") == {"noai"}

    def test_valued_directives_are_not_agent_scopes(self):
        # max-snippet:0 must not be mistaken for a "max-snippet" user agent.
        assert parse_x_robots_tag("max-snippet:0, noai") == {"max-snippet", "noai"}

    def test_unavailable_after_keeps_name_only(self):
        assert parse_x_robots_tag("unavailable_after: 25 Jun 2030 15:00:00 PST") == {"unavailable_after"}

    def test_empty_value(self):
        assert parse_x_robots_tag("") == set()


class TestParseRobotsMeta:
    def test_comma_separated(self):
        assert parse_robots_meta("noai, noindex") == {"noai", "noindex"}

    def test_space_separated_and_case(self):
        assert parse_robots_meta("NoAI  NOINDEX") == {"noai", "noindex"}

    def test_valued_directive_keeps_name(self):
        assert parse_robots_meta("noai, max-snippet:0") == {"noai", "max-snippet"}

    def test_empty(self):
        assert parse_robots_meta("") == set()


class TestEvaluateOptout:
    def test_noai_blocks_by_default_policy(self):
        decision = evaluate_optout({"noai"}, respect_noai=True, source="x-robots-tag")
        assert decision == OptOutDecision(blocked=True, matched=("noai",), source="x-robots-tag")

    def test_noimageai_blocks(self):
        decision = evaluate_optout({"noimageai"}, respect_noai=True, source="meta-robots")
        assert decision.blocked is True
        assert decision.matched == ("noimageai",)

    def test_respect_noai_false_allows(self):
        decision = evaluate_optout({"noai", "noimageai"}, respect_noai=False, source="x-robots-tag")
        assert decision.blocked is False
        assert decision.matched == ()

    def test_noindex_ignored_without_strict_flag(self):
        decision = evaluate_optout({"noindex"}, respect_noai=True, source="meta-robots")
        assert decision.blocked is False

    def test_noindex_blocks_with_strict_flag(self):
        decision = evaluate_optout({"noindex"}, respect_noai=True, respect_noindex=True, source="meta-robots")
        assert decision.blocked is True
        assert decision.matched == ("noindex",)

    def test_none_blocks_with_strict_flag(self):
        decision = evaluate_optout({"none"}, respect_noai=False, respect_noindex=True, source="x-robots-tag")
        assert decision.blocked is True
        assert decision.matched == ("none",)


@pytest.fixture
async def optout_server(monkeypatch):
    """Local aiohttp server with opted-out and plain pages."""
    from docpull.security.robots import RobotsChecker
    from docpull.security.url_validator import UrlValidationResult, UrlValidator

    async def blocked_header(_request: web.Request) -> web.Response:
        return web.Response(
            body=PLAIN_HTML,
            content_type="text/html",
            headers={"X-Robots-Tag": "noai"},
        )

    async def blocked_meta(_request: web.Request) -> web.Response:
        return web.Response(body=META_NOAI_HTML, content_type="text/html")

    async def plain(_request: web.Request) -> web.Response:
        return web.Response(body=PLAIN_HTML, content_type="text/html")

    async def robots(_request: web.Request) -> web.Response:
        return web.Response(text="", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/blocked-header", blocked_header)
    app.router.add_get("/blocked-meta", blocked_meta)
    app.router.add_get("/plain", plain)
    app.router.add_get("/robots.txt", robots)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server_socket = site._server.sockets[0]  # type: ignore[union-attr]
    port = server_socket.getsockname()[1]

    # docpull blocks loopback IPs and plain HTTP at validation time. Patch
    # the validator so the local test server is reachable; production code
    # is unchanged.
    def permissive_validate(self, hostname):
        return UrlValidationResult.valid()

    monkeypatch.setattr(UrlValidator, "validate_hostname", permissive_validate)

    original_init = UrlValidator.__init__

    def init_with_http(self, *args, **kwargs):
        kwargs["allowed_schemes"] = {"http", "https"}
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", init_with_http)

    # Bypass robots.txt fetching — the production checker enforces HTTPS-only.
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, url: [])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, url: None)

    yield {"base": f"http://127.0.0.1:{port}"}

    await runner.cleanup()


async def _run_fetcher(url: str, output_dir: Path) -> tuple[int, list[SkipReason | None]]:
    """Run the full Fetcher against one URL, return (fetched, skip_reasons)."""
    from docpull.core.fetcher import Fetcher
    from docpull.models.config import DocpullConfig

    config = DocpullConfig(
        url=url,
        output={"directory": output_dir},
        crawl={"max_pages": 1, "max_depth": 1},
    )
    skip_reasons: list[SkipReason | None] = []
    async with Fetcher(config) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_SKIPPED:
                skip_reasons.append(event.skip_reason)
    return fetcher.stats.pages_fetched, skip_reasons


def _saved_markdown(output_dir: Path) -> list[Path]:
    return [path for path in output_dir.rglob("*.md") if path.name != "sources.md"]


class TestPipelineEnforcement:
    async def test_x_robots_tag_noai_skips_page(self, optout_server, tmp_path: Path):
        output_dir = tmp_path / "out"
        fetched, skip_reasons = await _run_fetcher(f"{optout_server['base']}/blocked-header", output_dir)

        assert fetched == 0
        assert SkipReason.AI_OPTOUT in skip_reasons
        assert _saved_markdown(output_dir) == []

    async def test_meta_robots_noai_skips_page(self, optout_server, tmp_path: Path):
        output_dir = tmp_path / "out"
        fetched, skip_reasons = await _run_fetcher(f"{optout_server['base']}/blocked-meta", output_dir)

        assert fetched == 0
        assert SkipReason.AI_OPTOUT in skip_reasons
        assert _saved_markdown(output_dir) == []

    async def test_plain_page_unaffected(self, optout_server, tmp_path: Path):
        output_dir = tmp_path / "out"
        fetched, skip_reasons = await _run_fetcher(f"{optout_server['base']}/plain", output_dir)

        assert fetched == 1
        assert SkipReason.AI_OPTOUT not in skip_reasons
        assert _saved_markdown(output_dir) != []


class _DirectClient:
    """Minimal HttpClient adapter for step-level tests against the server."""

    async def get(self, url, *, timeout=30.0, headers=None):
        import aiohttp

        from docpull.http.protocols import HttpResponse

        async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
            content = await resp.read()
            return HttpResponse(
                status_code=resp.status,
                content=content,
                content_type=resp.content_type or "",
                headers=dict(resp.headers),
                url=str(resp.url),
            )


class TestFetchStepOptOut:
    async def test_header_optout_blocks_by_default(self, optout_server, tmp_path: Path):
        step = FetchStep(http_client=_DirectClient())
        ctx = PageContext(
            url=f"{optout_server['base']}/blocked-header",
            output_path=tmp_path / "page.md",
        )
        events = []
        result = await step.execute(ctx, emit=events.append)

        assert result.should_skip is True
        assert result.skip_code == SkipReason.AI_OPTOUT
        assert "x-robots-tag" in (result.skip_reason or "")
        assert result.html is None
        assert any(e.skip_reason == SkipReason.AI_OPTOUT for e in events)

    async def test_no_respect_ai_optout_fetches_anyway(self, optout_server, tmp_path: Path):
        step = FetchStep(http_client=_DirectClient(), respect_ai_optout=False)
        ctx = PageContext(
            url=f"{optout_server['base']}/blocked-header",
            output_path=tmp_path / "page.md",
        )
        result = await step.execute(ctx)

        assert result.should_skip is False
        assert result.html == PLAIN_HTML

    async def test_noindex_header_needs_strict_flag(self, tmp_path: Path):
        from unittest.mock import AsyncMock, MagicMock

        def response_with(header_value: str):
            response = MagicMock()
            response.status_code = 200
            response.content = PLAIN_HTML
            response.content_type = "text/html"
            response.headers = {"X-Robots-Tag": header_value}
            return response

        client = AsyncMock()
        client.get.return_value = response_with("noindex")

        default_step = FetchStep(http_client=client)
        ctx = PageContext(url="https://example.com/a", output_path=tmp_path / "a.md")
        assert (await default_step.execute(ctx)).should_skip is False

        strict_step = FetchStep(http_client=client, respect_noindex=True)
        ctx = PageContext(url="https://example.com/a", output_path=tmp_path / "a.md")
        result = await strict_step.execute(ctx)
        assert result.should_skip is True
        assert result.skip_code == SkipReason.AI_OPTOUT


class TestConvertStepOptOut:
    def _ctx(self, html: bytes, tmp_path: Path) -> PageContext:
        ctx = PageContext(url="https://example.com/page", output_path=tmp_path / "page.md")
        ctx.html = html
        ctx.content_type = "text/html"
        return ctx

    async def test_meta_noai_blocks_by_default(self, tmp_path: Path):
        step = ConvertStep()
        result = await step.execute(self._ctx(META_NOAI_HTML, tmp_path))

        assert result.should_skip is True
        assert result.skip_code == SkipReason.AI_OPTOUT
        assert "meta robots" in (result.skip_reason or "")
        assert result.markdown is None

    async def test_meta_noai_override_converts(self, tmp_path: Path):
        step = ConvertStep(respect_ai_optout=False)
        result = await step.execute(self._ctx(META_NOAI_HTML, tmp_path))

        assert result.should_skip is False
        assert result.markdown

    async def test_meta_noindex_needs_strict_flag(self, tmp_path: Path):
        default_step = ConvertStep()
        result = await default_step.execute(self._ctx(META_NOINDEX_HTML, tmp_path))
        assert result.should_skip is False
        assert result.markdown

        strict_step = ConvertStep(respect_noindex=True)
        result = await strict_step.execute(self._ctx(META_NOINDEX_HTML, tmp_path))
        assert result.should_skip is True
        assert result.skip_code == SkipReason.AI_OPTOUT

    async def test_docpull_scoped_meta_applies(self, tmp_path: Path):
        html = (
            b"<!doctype html><html><head><meta name='docpull' content='noai'></head>"
            b"<body><article><h1>Scoped</h1><p>Body text.</p></article></body></html>"
        )
        step = ConvertStep()
        result = await step.execute(self._ctx(html, tmp_path))

        assert result.should_skip is True
        assert result.skip_code == SkipReason.AI_OPTOUT

    async def test_plain_html_unaffected(self, tmp_path: Path):
        step = ConvertStep()
        result = await step.execute(self._ctx(PLAIN_HTML, tmp_path))

        assert result.should_skip is False
        assert result.markdown


class TestCliFlags:
    def test_defaults(self):
        from docpull.cli import create_parser

        args = create_parser().parse_args(["https://example.com"])
        assert args.respect_ai_optout is True
        assert args.respect_noindex is False

    def test_flags_parse(self):
        from docpull.cli import create_parser

        args = create_parser().parse_args(
            ["https://example.com", "--no-respect-ai-optout", "--respect-noindex"]
        )
        assert args.respect_ai_optout is False
        assert args.respect_noindex is True

    def test_flags_plumb_to_config(self, monkeypatch, tmp_path: Path):
        from docpull.cli import create_parser, run_fetcher

        captured = {}

        class FakeFetcher:
            def __init__(self, config):
                captured["config"] = config
                self.config = config
                self.stats = SimpleNamespace(
                    urls_discovered=1,
                    pages_fetched=1,
                    pages_skipped=0,
                    pages_failed=0,
                    duration_seconds=0.1,
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

            async def run(self):
                for event in []:
                    yield event

        monkeypatch.setattr("docpull.cli.Fetcher", FakeFetcher)
        args = create_parser().parse_args(
            [
                "https://example.com",
                "--quiet",
                "-o",
                str(tmp_path),
                "--no-respect-ai-optout",
                "--respect-noindex",
            ]
        )
        run_fetcher(args)

        config = captured["config"]
        assert config.respect_ai_optout is False
        assert config.respect_noindex is True

    def test_config_defaults(self):
        from docpull.models.config import DocpullConfig

        config = DocpullConfig()
        assert config.respect_ai_optout is True
        assert config.respect_noindex is False

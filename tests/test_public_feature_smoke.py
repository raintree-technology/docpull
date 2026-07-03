"""Local smoke tests for the retained public release feature surface."""

from __future__ import annotations

import importlib.util
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from docpull import auth_cli
from docpull import cli as cli_module
from docpull import project as project_module
from docpull.cli import main
from docpull.exports import EXPORT_FORMATS
from docpull.models.events import EventType
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidationResult, UrlValidator


def _run_cli(args: list[str]) -> None:
    try:
        code = main(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 0, "docpull " + " ".join(args)


@pytest.fixture
def local_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Alpha Docs</title></head>
  <body>
    <main>
      <h1>Alpha Docs</h1>
      <p>Alpha API returns cited JSON results for local smoke tests.</p>
      <a href="/guide.html">Guide</a>
    </main>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (site_dir / "guide.html").write_text(
        """<!doctype html>
<html>
  <head><title>Alpha Guide</title></head>
  <body><article><h1>Alpha Guide</h1><p>Use Alpha pricing carefully.</p></article></body>
</html>
""",
        encoding="utf-8",
    )

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(QuietHandler, directory=str(site_dir)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    original_init = UrlValidator.__init__

    def permissive_init(self: UrlValidator, *args: Any, **kwargs: Any) -> None:
        kwargs["allowed_schemes"] = {"http", "https"}
        kwargs["block_private_ips"] = False
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", permissive_init)
    monkeypatch.setattr(
        UrlValidator,
        "validate_hostname",
        lambda self, _hostname: UrlValidationResult.valid(),
    )
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, _url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, _url: [])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, _url: None)

    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_text_pack(tmp_path: Path, *, name: str = "pack", content: str | None = None) -> Path:
    source = tmp_path / f"{name}.txt"
    source.write_text(
        content
        or (
            "Alpha API returns cited JSON results for local smoke tests.\n"
            "Alpha pricing is current. Contact support@example.com for help.\n"
        ),
        encoding="utf-8",
    )
    pack_dir = tmp_path / name
    _run_cli(
        [
            "parse",
            str(source),
            "-o",
            str(pack_dir),
            "--backend",
            "text",
            "--eval-grade",
            "--format",
            "json",
        ]
    )
    _run_cli(["pack", "validate", str(pack_dir), "--level", "eval", "--format", "json"])
    return pack_dir


def test_root_fetch_profiles_and_output_formats(local_site: str, tmp_path: Path) -> None:
    for output_format in ("markdown", "json", "ndjson", "sqlite", "okf"):
        output_dir = tmp_path / f"format-{output_format}"
        _run_cli(
            [
                local_site,
                "--single",
                "--format",
                output_format,
                "-o",
                str(output_dir),
                "--quiet",
            ]
        )
        _run_cli(["pack", "validate", str(output_dir), "--level", "raw"])

    for profile in ("rag", "mirror", "quick", "llm", "okf", "sec-filing"):
        output_dir = tmp_path / f"profile-{profile}"
        _run_cli(
            [
                local_site,
                "--single",
                "--profile",
                profile,
                "-o",
                str(output_dir),
                "--quiet",
            ]
        )
        assert (output_dir / "corpus.manifest.json").exists()

    dry_run_dir = tmp_path / "dry-run"
    _run_cli([local_site, "--dry-run", "-o", str(dry_run_dir), "--quiet"])
    _run_cli([local_site, "--preview-urls", "--max-pages", "2", "-o", str(tmp_path / "preview"), "--quiet"])


def test_pack_graph_export_monitor_ci_and_refresh_public_surface(tmp_path: Path) -> None:
    pack_dir = _write_text_pack(tmp_path, name="alpha-pack")
    newer_pack = _write_text_pack(
        tmp_path,
        name="alpha-pack-new",
        content="Alpha API now returns cited JSON results with updated pricing.\n",
    )

    _run_cli(["pack", "validate", str(pack_dir), "--level", "raw"])
    _run_cli(["pack", "validate", str(pack_dir), "--level", "agent", "--format", "json"])
    _run_cli(["pack", "score", str(pack_dir), "--output", str(tmp_path / "score.json")])
    _run_cli(
        [
            "pack",
            "audit",
            str(pack_dir),
            "--output",
            str(tmp_path / "audit.json"),
            "--markdown",
            str(tmp_path / "audit.md"),
        ]
    )
    _run_cli(["pack", "publish", str(pack_dir), "--target", "agent-docs"])
    _run_cli(["pack", "basis", str(pack_dir), "--claim", "Alpha returns cited JSON results"])
    _run_cli(["pack", "redact", str(pack_dir), "-o", str(tmp_path / "redacted")])
    _run_cli(["pack", "diff", str(pack_dir), str(newer_pack), "--output", str(tmp_path / "diff.json")])
    _run_cli(["pack", "sources", str(pack_dir), "--output", str(tmp_path / "sources.json")])
    _run_cli(
        [
            "pack",
            "citations",
            str(pack_dir),
            "--output",
            str(tmp_path / "citations.json"),
            "--markdown",
            str(tmp_path / "citations.md"),
        ]
    )
    _run_cli(["pack", "entities", str(pack_dir), "--output", str(tmp_path / "entities.json")])
    _run_cli(["pack", "search", str(pack_dir), "Alpha", "--output", str(tmp_path / "search.json")])
    _run_cli(
        [
            "pack",
            "brief",
            str(pack_dir),
            "--objective",
            "Review Alpha evidence",
            "--output",
            str(tmp_path / "brief.md"),
            "--json-output",
            str(tmp_path / "brief.json"),
        ]
    )
    _run_cli(["pack", "prepare", str(pack_dir), "--eval-grade", "--output", str(tmp_path / "prepare.json")])

    _run_cli(["graph", "build", str(pack_dir), "--entity-limit", "10"])
    _run_cli(["graph", "status", str(pack_dir)])
    _run_cli(["graph", "query", str(pack_dir), "Alpha", "--limit", "5"])
    _run_cli(["graph", "neighbors", str(pack_dir), "Alpha", "--limit", "5"])
    _run_cli(["graph", "refresh", str(pack_dir), "--entity-limit", "10"])

    export_root = tmp_path / "exports"
    for export_format in EXPORT_FORMATS:
        if export_format == "parquet" and importlib.util.find_spec("pyarrow") is None:
            continue
        if export_format in {"claude-skill", "codex-skill"}:
            output = export_root / export_format
        elif export_format == "cursor-rules":
            output = export_root / "alpha.mdc"
        elif export_format == "sheets-csv":
            output = export_root / "alpha.csv"
        elif export_format == "sheets-tsv":
            output = export_root / "alpha.tsv"
        elif export_format == "warehouse-ndjson":
            output = export_root / "alpha.ndjson"
        elif export_format == "parquet":
            output = export_root / "alpha.parquet"
        elif export_format.endswith("-json"):
            output = export_root / f"{export_format}.json"
        else:
            output = export_root / f"{export_format}.jsonl"
        _run_cli(
            [
                "export",
                str(pack_dir),
                "--format",
                export_format,
                "-o",
                str(output),
                "--skill-name",
                "alpha-pack",
            ]
        )

    _run_cli(["ci", str(pack_dir), "--prepare", "--json", "--min-pack-score", "0", "--min-audit-score", "0"])
    _run_cli(["refresh", str(pack_dir), "--dry-run", "-o", str(tmp_path / "refresh")])

    state_dir = tmp_path / "monitor-state"
    _run_cli(["monitor", "--state-dir", str(state_dir), "init", str(pack_dir), "--name", "alpha"])
    _run_cli(["monitor", "--state-dir", str(state_dir), "list", "--json"])
    _run_cli(["monitor", "--state-dir", str(state_dir), "run", "alpha", "--once", "--dry-run", "--json"])
    _run_cli(["monitor", "--state-dir", str(state_dir), "trigger", "alpha", "--dry-run", "--json"])
    _run_cli(["monitor", "--state-dir", str(state_dir), "pause", "alpha", "--json"])
    _run_cli(["monitor", "--state-dir", str(state_dir), "unpause", "alpha", "--json"])
    _run_cli(["monitor", "--state-dir", str(state_dir), "report", "alpha", "--json"])
    _run_cli(
        [
            "monitor",
            "--state-dir",
            str(state_dir),
            "scheduler-snippet",
            "alpha",
            "--kind",
            "cron",
            "--json",
        ]
    )


def test_project_policy_auth_openapi_render_and_server_public_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProjectFetcher:
        calls = 0

        def __init__(self, config: Any) -> None:
            self.config = config
            self.stats = SimpleNamespace(pages_fetched=1, pages_failed=0, pages_skipped=0)

        async def __aenter__(self) -> FakeProjectFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def run(self):  # type: ignore[no-untyped-def]
            FakeProjectFetcher.calls += 1
            output_dir = self.config.output.directory
            output_dir.mkdir(parents=True, exist_ok=True)
            content = f"Alpha project API context run {FakeProjectFetcher.calls}."
            record = {
                "schema_version": 3,
                "document_id": f"doc_alpha_{FakeProjectFetcher.calls}",
                "chunk_id": f"chunk_alpha_{FakeProjectFetcher.calls}",
                "url": self.config.url,
                "title": "Alpha Project",
                "content": content,
                "content_hash": f"hash_{FakeProjectFetcher.calls}",
                "source_type": "html",
                "fetched_at": "2026-07-02T00:00:00+00:00",
                "content_type": "text/html",
                "mime_type": "text/html",
                "token_count": 12,
                "metadata": {},
                "extraction": {},
                "route": {"name": "test-fake-fetch"},
                "rights": {"state": "unknown"},
            }
            (output_dir / "documents.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")
            yield SimpleNamespace(type=EventType.STARTED, message="started")
            yield SimpleNamespace(type=EventType.COMPLETED, message="done")

    async def fake_auth_check(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {
            "schema_version": 1,
            "generated_at": "2026-07-02T00:00:00+00:00",
            "url": "https://docs.example.com/private",
            "host": "docs.example.com",
            "ok": True,
            "auth_policy": "explicit-private",
            "auth_type": "bearer",
            "status_code": 204,
            "content_type": "text/html",
            "bytes_downloaded": 42,
            "skip_reason": None,
            "error": None,
            "secret_handling": "Credential values are never included in this report.",
        }

    monkeypatch.setattr(project_module, "Fetcher", FakeProjectFetcher)
    monkeypatch.setattr(auth_cli, "auth_check", fake_auth_check)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)

    _run_cli(["init", "alpha-project"])
    _run_cli(["add", "https://docs.example.com", "--name", "alpha-docs"])
    _run_cli(["install", "--json"])
    _run_cli(["deps", "--json"])
    _run_cli(["sources", "list"])
    _run_cli(["sync", "--run-id", "run_one", "--json"])
    _run_cli(["sync", "--run-id", "run_two", "--json"])
    _run_cli(["diff", "--from", "run_one", "--to", "run_two", "--json", "--semantic", "off"])
    _run_cli(["status", "--json"])
    _run_cli(["history", "--json"])
    _run_cli(["review", "--run", "run_two", "--json"])
    _run_cli(["release", "context-pack", "--target", "openai", "--run", "run_two", "--tag", "v-test"])
    _run_cli(["watch", "https://docs.example.com", "--export", "openai"])

    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        """
schema_version: 1
allowed_domains:
  - docs.example.com
providers:
  allowed:
    - local
  max_estimated_cost_usd: 0
auth:
  allow_authenticated_sources: false
""",
        encoding="utf-8",
    )
    _run_cli(["policy", "validate", str(policy_path), "--json"])
    _run_cli(["policy", "explain", str(policy_path), "--json"])
    _run_cli(["policy", "redaction", "init", "-o", str(tmp_path / "redaction.yml"), "--json"])

    _run_cli(
        [
            "auth",
            "check",
            "https://docs.example.com/private",
            "--auth-policy",
            "explicit-private",
            "--auth-bearer",
            "secret",
            "--json",
            "--output",
            str(tmp_path / "auth.json"),
        ]
    )

    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "Alpha API", "version": "1.0.0"},
                "paths": {
                    "/health": {
                        "get": {
                            "summary": "Health check",
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _run_cli(["openapi-pack", str(spec_path), "-o", str(tmp_path / "openapi-pack"), "--json"])

    feed_path = tmp_path / "feed.xml"
    feed_path.write_text(
        """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Alpha News</title>
    <item>
      <title>Alpha Event</title>
      <link>https://docs.example.com/news/alpha-event</link>
      <pubDate>Thu, 02 Jul 2026 10:00:00 GMT</pubDate>
      <description>Alpha event context is available as feed evidence.</description>
    </item>
  </channel>
</rss>
""",
        encoding="utf-8",
    )
    _run_cli(["feed-pack", str(feed_path), "-o", str(tmp_path / "feed-pack"), "--json"])
    monkeypatch.setattr(
        cli_module,
        "check_render_backend_availability",
        lambda backend, binary=None: (True, f"[OK] {backend}: checked"),
    )
    _run_cli(["render", "--check"])
    _run_cli(["mcp", "--help"])
    _run_cli(["serve", "--help"])
    report = tmp_path / "report.md"
    report.write_text("# Report\n\nSmoke report.", encoding="utf-8")
    _run_cli(["share", "--help"])

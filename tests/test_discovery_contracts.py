"""Provider-neutral discovery contract tests."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull import discovery_cli
from docpull.discovery.contracts import (
    CandidateSourceRecord,
    normalize_provider_response,
    read_candidate_records,
    records_from_site_scan,
    records_from_sitemap_file,
    select_candidate_records,
)
from docpull.http.protocols import HttpResponse
from docpull.policy import PolicyConfig


def main(argv: list[str]) -> int:
    command, *rest = argv
    if command == "discover":
        return discovery_cli.run_discovery_cli(rest)
    raise AssertionError(f"Unexpected discovery CLI command: {command}")


def _candidate(url: str, *, score: float, rank: int = 1, title: str | None = None) -> CandidateSourceRecord:
    return CandidateSourceRecord(
        generated_at="2026-06-19T00:00:00+00:00",
        url=url,
        source="test",
        title=title,
        provider="local",
        score=score,
        rank=rank,
        discovered_at="2026-06-19T00:00:00+00:00",
    )


class _FakeHttpClient:
    def __init__(self, routes: dict[str, tuple[bytes | str, str]]) -> None:
        self.routes = routes

    async def get(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        if url not in self.routes:
            return HttpResponse(status_code=404, content=b"", content_type="text/plain", headers={}, url=url)
        body, content_type = self.routes[url]
        content = body.encode("utf-8") if isinstance(body, str) else body
        return HttpResponse(status_code=200, content=content, content_type=content_type, headers={}, url=url)

    async def head(
        self,
        url: str,
        *,
        timeout: float = 10.0,
    ) -> HttpResponse:
        return await self.get(url, timeout=timeout)


def test_discover_positive_int_rejects_invalid_values() -> None:
    assert discovery_cli._positive_int("3") == 3
    with pytest.raises(argparse.ArgumentTypeError, match="integer"):
        discovery_cli._positive_int("not-a-number")
    with pytest.raises(argparse.ArgumentTypeError, match="at least 1"):
        discovery_cli._positive_int("0")


def test_provider_import_normalizes_search_results_without_copying_secret_fields(tmp_path: Path) -> None:
    response = tmp_path / "provider.json"
    response.write_text(
        json.dumps(
            {
                "query": "agent search docs",
                "request_options": {"api_key": "test-secret"},
                "search": {
                    "results": [
                        {
                            "url": "https://docs.example.com/api/search",
                            "title": "Search API",
                            "excerpts": ["Search docs excerpt."],
                            "score": 0.91,
                            "api_key": "test-secret",
                        },
                        {
                            "url": "https://docs.example.com/api/search",
                            "title": "Duplicate",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    records = normalize_provider_response(
        response,
        provider="exa",
        expected_domains=["docs.example.com"],
    )

    assert len(records) == 1
    assert records[0].provider == "exa"
    assert records[0].score is not None and records[0].score >= 91
    assert records[0].metadata["provider_score"] == 0.91
    assert records[0].query == "agent search docs"
    assert "test-secret" not in json.dumps(records[0].model_dump(mode="json"))


def test_discover_import_cli_normalizes_provider_response(tmp_path: Path, capsys) -> None:
    response = tmp_path / "provider.json"
    response.write_text(
        json.dumps(
            {
                "query": "agent docs",
                "results": [
                    {
                        "url": "https://docs.example.com/api/import",
                        "title": "Imported result",
                        "score": 0.75,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "discovery"

    assert (
        main(
            [
                "discover",
                "import",
                str(response),
                "--provider",
                "exa",
                "--include-domain",
                "docs.example.com",
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_count"] == 1
    records = read_candidate_records(output_dir)
    assert records[0].source == "provider-import:exa"
    assert records[0].metadata["imported_from"] == "provider.json"


def test_discover_urls_cli_writes_required_sidecars_and_policy_filters(
    tmp_path: Path,
    capsys,
) -> None:
    policy = tmp_path / "policy.yml"
    policy.write_text(
        """
schema_version: 1
allowed_domains:
  - docs.example.com
denied_paths:
  - /admin/*
""",
        encoding="utf-8",
    )
    urls = tmp_path / "urls.txt"
    urls.write_text(
        "\n".join(
            [
                "https://docs.example.com/api/reference",
                "https://docs.example.com/admin/private",
                "https://other.example.com/page",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "discovery"

    assert (
        main(
            [
                "discover",
                "urls",
                str(urls),
                "--policy",
                str(policy),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_count"] == 1
    assert payload["skipped_count"] == 2
    assert (output_dir / "candidate_sources.ndjson").exists()
    assert (output_dir / "source_policy.json").exists()
    assert (output_dir / "DISCOVERY.md").exists()

    records = read_candidate_records(output_dir)
    assert [record.url for record in records] == ["https://docs.example.com/api/reference"]
    source_policy = json.loads((output_dir / "source_policy.json").read_text(encoding="utf-8"))
    assert source_policy["constraints"]["allowed_domains"] == ["docs.example.com"]


def test_local_sitemap_file_is_deterministic_and_base_host_scoped(tmp_path: Path) -> None:
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/alpha</loc></url>
  <url><loc>https://other.example.com/beta</loc></url>
</urlset>
""",
        encoding="utf-8",
    )

    records = records_from_sitemap_file(
        sitemap,
        base_url="https://docs.example.com",
        expected_domains=["docs.example.com"],
    )

    assert [record.url for record in records] == ["https://docs.example.com/alpha"]
    assert records[0].source == "local-sitemap"


def test_discover_sitemap_cli_and_select_cli_write_reports(tmp_path: Path, capsys) -> None:
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/alpha</loc></url>
  <url><loc>https://docs.example.com/beta</loc></url>
</urlset>
""",
        encoding="utf-8",
    )
    discovery_dir = tmp_path / "discovery"

    assert (
        main(
            [
                "discover",
                "sitemap",
                str(sitemap),
                "--base-url",
                "https://docs.example.com",
                "--include-domain",
                "docs.example.com",
                "--max-results",
                "1",
                "--output-dir",
                str(discovery_dir),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["candidate_count"] == 1

    selected_dir = tmp_path / "selected"
    assert (
        main(
            [
                "discover",
                "select",
                str(discovery_dir),
                "--select",
                "top:1",
                "--output-dir",
                str(selected_dir),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_count"] == 1
    assert (selected_dir / "selected_sources.ndjson").exists()


def test_site_scan_collects_local_open_discovery_candidates() -> None:
    routes = {
        "https://docs.example.com": (
            """
<html><head>
  <title>Example Website</title>
  <link rel="canonical" href="/">
  <link rel="alternate" type="application/rss+xml" href="/feed.xml">
  <link rel="service-desc" href="/openapi.json">
</head><body>
  <a href="/pricing">Pricing</a>
  <a href="/about">About</a>
</body></html>
""",
            "text/html",
        ),
        "https://docs.example.com/llms.txt": (
            "# Docs\n\n- [Guide](/guide)\n- [API](https://docs.example.com/api)",
            "text/plain",
        ),
        "https://docs.example.com/feed.xml": (
            """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Release Notes</title>
    <link>https://docs.example.com/releases</link>
    <description>Latest release.</description>
  </item>
</channel></rss>
""",
            "application/rss+xml",
        ),
        "https://docs.example.com/openapi.json": (
            json.dumps(
                {
                    "openapi": "3.1.0",
                    "info": {"title": "Example API", "description": "Example API spec."},
                    "externalDocs": {"url": "/api/reference", "description": "API reference"},
                    "paths": {},
                }
            ),
            "application/json",
        ),
        "https://docs.example.com/robots.txt": (
            "User-agent: *\nSitemap: https://docs.example.com/sitemap.xml\n",
            "text/plain",
        ),
        "https://docs.example.com/sitemap.xml": (
            """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.example.com/docs-sitemap.xml</loc></sitemap>
</sitemapindex>
""",
            "application/xml",
        ),
        "https://docs.example.com/docs-sitemap.xml": (
            """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/reference/sitemap-page</loc></url>
</urlset>
""",
            "application/xml",
        ),
    }

    records = asyncio.run(
        records_from_site_scan(
            "https://docs.example.com",
            client=_FakeHttpClient(routes),
            expected_domains=["docs.example.com"],
        )
    )

    by_url = {record.url: record for record in records}
    assert "https://docs.example.com/llms.txt" in by_url
    assert "https://docs.example.com" in by_url
    assert "https://docs.example.com/pricing" in by_url
    assert "https://docs.example.com/about" in by_url
    assert "https://docs.example.com/guide" in by_url
    assert "https://docs.example.com/releases" in by_url
    assert "https://docs.example.com/openapi.json" in by_url
    assert "https://docs.example.com/api/reference" in by_url
    assert "https://docs.example.com/reference/sitemap-page" in by_url
    assert {record.metadata["discovery_engine"] for record in records} >= {
        "links",
        "llms",
        "feeds",
        "openapi",
        "sitemaps",
    }


def test_site_scan_collects_github_docs_tree_candidates() -> None:
    routes = {
        "https://api.github.com/repos/owner/project/contents?ref=HEAD": (
            json.dumps(
                [
                    {
                        "type": "file",
                        "path": "README.md",
                        "download_url": "https://raw.githubusercontent.com/owner/project/main/README.md",
                        "html_url": "https://github.com/owner/project/blob/main/README.md",
                    },
                    {"type": "dir", "path": "docs"},
                ]
            ),
            "application/json",
        ),
        "https://api.github.com/repos/owner/project/contents/docs?ref=HEAD": (
            json.dumps(
                [
                    {
                        "type": "file",
                        "path": "docs/install.md",
                        "download_url": "https://raw.githubusercontent.com/owner/project/main/docs/install.md",
                        "html_url": "https://github.com/owner/project/blob/main/docs/install.md",
                    },
                    {"type": "dir", "path": "docs/reference"},
                ]
            ),
            "application/json",
        ),
        "https://api.github.com/repos/owner/project/contents/docs/reference?ref=HEAD": (
            json.dumps(
                [
                    {
                        "type": "file",
                        "path": "docs/reference/api.mdx",
                        "download_url": "https://raw.githubusercontent.com/owner/project/main/docs/reference/api.mdx",
                        "html_url": "https://github.com/owner/project/blob/main/docs/reference/api.mdx",
                    }
                ]
            ),
            "application/json",
        ),
    }

    records = asyncio.run(
        records_from_site_scan(
            "https://github.com/owner/project",
            client=_FakeHttpClient(routes),
            sources=["github"],
            expected_domains=["github.com", "raw.githubusercontent.com"],
        )
    )

    assert [record.url for record in records] == [
        "https://raw.githubusercontent.com/owner/project/main/README.md",
        "https://raw.githubusercontent.com/owner/project/main/docs/install.md",
        "https://raw.githubusercontent.com/owner/project/main/docs/reference/api.mdx",
    ]
    assert records[1].metadata["github_path"] == "docs/install.md"


def test_discover_scan_cli_writes_candidate_pack_without_live_network(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    routes = {
        "https://docs.example.com": ("<html></html>", "text/html"),
        "https://docs.example.com/llms.txt": ("[Guide](/guide)", "text/plain"),
    }

    class FakeAsyncHttpClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.client = _FakeHttpClient(routes)

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, *_exc) -> None:
            return None

    class FakeUrlValidator:
        def validate(self, _url: str):
            return SimpleNamespace(is_valid=True, rejection_reason=None)

    monkeypatch.setattr(discovery_cli, "AsyncHttpClient", FakeAsyncHttpClient)
    monkeypatch.setattr(discovery_cli, "UrlValidator", FakeUrlValidator)
    output_dir = tmp_path / "scan"

    assert (
        main(
            [
                "discover",
                "scan",
                "https://docs.example.com",
                "--source",
                "llms",
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_count"] == 2
    records = read_candidate_records(output_dir)
    assert [record.url for record in records] == [
        "https://docs.example.com/llms.txt",
        "https://docs.example.com/guide",
    ]


def test_selection_policies_top_domain_score_and_manual_file(tmp_path: Path) -> None:
    records = [
        _candidate("https://docs.example.com/a", score=90, rank=2),
        _candidate("https://docs.example.com/b", score=80, rank=3),
        _candidate("https://api.example.com/c", score=75, rank=4),
        _candidate("https://blog.example.com/d", score=40, rank=1),
    ]

    selected = select_candidate_records(records, ["score>=70", "domain:1", "top:2"])
    assert [record.url for record in selected] == [
        "https://docs.example.com/a",
        "https://api.example.com/c",
    ]

    manual = tmp_path / "manual.txt"
    manual.write_text(
        "https://docs.example.com/b\nhttps://manual.example.com/new\n",
        encoding="utf-8",
    )
    selected = select_candidate_records(records, ["manual-file"], manual_file=manual)

    assert [record.url for record in selected] == [
        "https://docs.example.com/b",
        "https://manual.example.com/new",
    ]
    assert selected[1].source == "manual-file"


def test_discover_fetch_dry_run_writes_selected_artifacts(tmp_path: Path, capsys) -> None:
    discovery_dir = tmp_path / "discovery"
    policy = PolicyConfig(allowed_domains=["docs.example.com"])
    records = [
        _candidate("https://docs.example.com/a", score=90, rank=1),
        _candidate("https://docs.example.com/b", score=60, rank=2),
    ]
    from docpull.discovery.contracts import write_discovery_pack

    write_discovery_pack(discovery_dir, records, policy=policy, source="test")

    selected_dir = tmp_path / "selected"
    assert (
        main(
            [
                "discover",
                "fetch",
                str(discovery_dir),
                "--select",
                "top:1",
                "--dry-run",
                "--output-dir",
                str(selected_dir),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["selected_count"] == 1
    assert (selected_dir / "selected_sources.ndjson").exists()
    assert (selected_dir / "selected_urls.txt").read_text(encoding="utf-8") == "https://docs.example.com/a\n"


def test_discover_fetch_uses_selected_sources_without_network(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class FakeFetcher:
        def __init__(self, _config) -> None:
            self.stats = SimpleNamespace(pages_fetched=0, pages_skipped=0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def fetch_one(self, _url: str):
            self.stats.pages_fetched += 1
            return SimpleNamespace(error=None, should_skip=False, skip_reason=None, skip_code=None)

    discovery_dir = tmp_path / "discovery"
    write_records = [
        _candidate("https://docs.example.com/a", score=90, rank=1),
        _candidate("https://docs.example.com/b", score=80, rank=2),
    ]
    from docpull.discovery.contracts import write_discovery_pack

    write_discovery_pack(discovery_dir, write_records, policy=PolicyConfig(), source="test")
    monkeypatch.setattr(discovery_cli, "Fetcher", FakeFetcher)

    assert (
        main(
            [
                "discover",
                "fetch",
                str(discovery_dir),
                "--select",
                "top:2",
                "--output-dir",
                str(tmp_path / "fetched"),
                "--json",
                "--quiet",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is False
    assert payload["fetched"] == 2


def test_discover_fetch_reports_empty_selection_and_fetch_failures(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    empty_dir = tmp_path / "empty-discovery"
    from docpull.discovery.contracts import write_discovery_pack

    write_discovery_pack(empty_dir, [], policy=PolicyConfig(), source="test")

    assert main(["discover", "fetch", str(empty_dir), "--output-dir", str(tmp_path / "none")]) == 0
    assert "No selected sources" in capsys.readouterr().out

    class FailingFetcher:
        def __init__(self, _config) -> None:
            self.stats = SimpleNamespace(pages_fetched=0, pages_skipped=0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def fetch_one(self, _url: str):
            return SimpleNamespace(
                error="network failed",
                should_skip=False,
                skip_reason=None,
                skip_code=None,
            )

    discovery_dir = tmp_path / "discovery"
    write_discovery_pack(
        discovery_dir,
        [_candidate("https://docs.example.com/a", score=90, rank=1)],
        policy=PolicyConfig(),
        source="test",
    )
    monkeypatch.setattr(discovery_cli, "Fetcher", FailingFetcher)

    assert main(["discover", "fetch", str(discovery_dir), "--output-dir", str(tmp_path / "failed")]) == 1
    assert "network failed" in capsys.readouterr().out

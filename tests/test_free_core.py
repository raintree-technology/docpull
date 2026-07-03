"""Free-core parity command tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull.agent_publish import publish_agent_docs
from docpull.cli import main
from docpull.context_packs.common import (
    allowed_by_domains,
    asset_allowed_domains_for_domain,
)
from docpull.discovery.contracts import CandidateSourceRecord
from docpull.free_core import (
    batch_scrape,
    crawl_url,
    map_url,
    run_answer_top_cli,
    run_brief_cli,
    run_crawl_url_cli,
    run_entities_top_cli,
    run_images_cli,
    scrape_url,
)
from docpull.free_core_smoke import run_free_core_smoke_cli
from docpull.models.events import SkipReason
from docpull.monitor import classify_pack_changes
from docpull.pack_tools import DEFAULT_BRIEF_ENTITY_LIMIT
from docpull.policy import PolicyConfig
from docpull.redaction import redact_pack, scan_sensitive_content, write_default_redaction_policy

from .pack_fixtures import write_context_pack

pytestmark = pytest.mark.internal_legacy


class _FakeParityFetcher:
    def __init__(self, config):
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def fetch_one(self, url: str, *, save: bool = False):
        title = url.rsplit("/", 1)[-1] or "home"
        return SimpleNamespace(
            error=None,
            should_skip=False,
            skip_reason=None,
            markdown=f"# {title}\n\nContact dev@example.com. Pricing and product details for {url}.",
            title=title.title(),
            metadata={},
            extraction_info={},
            source_type="test",
        )


class _FakeDeepFetcher:
    discover_urls: list[str] = []
    outcomes: dict[str, SimpleNamespace] = {}
    configs: list[object] = []

    def __init__(self, config):
        self.config = config
        self.__class__.configs.append(config)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def discover(self):
        return list(self.__class__.discover_urls)

    async def fetch_one(self, url: str, *, save: bool = False):
        if url in self.__class__.outcomes:
            return self.__class__.outcomes[url]
        title = url.rsplit("/", 1)[-1] or "home"
        return SimpleNamespace(
            error=None,
            should_skip=False,
            skip_reason=None,
            skip_code=None,
            status_code=200,
            content_type="text/html",
            markdown=f"# {title}\n\nDeep crawl content for {url}.",
            title=title.title(),
            metadata={},
            extraction_info={},
            source_type="test",
        )


def test_agent_publish_writes_agent_docs(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    payload = publish_agent_docs(pack_dir)

    assert payload["target"] == "agent-docs"
    for filename in [
        "AGENT_CONTEXT.md",
        "llms.txt",
        "llms-full.txt",
        "MCP_SNIPPETS.md",
        "INSTALL.md",
        "SOURCE_INDEX.md",
    ]:
        assert (pack_dir / filename).exists()
    assert "Citation Rules" in (pack_dir / "AGENT_CONTEXT.md").read_text(encoding="utf-8")


def test_map_url_writes_discovery_artifacts(tmp_path: Path, monkeypatch) -> None:
    async def fake_scan(url: str, *, policy: PolicyConfig, max_per_source: int):
        assert url == "https://docs.example.com"
        assert max_per_source == 50
        return [
            CandidateSourceRecord(
                url="https://docs.example.com/guide",
                source="llms",
                title="Guide",
                score=90,
            ),
            CandidateSourceRecord(
                url="https://sponsor.example.net/ad",
                source="links",
                title="Sponsor",
                score=100,
            ),
        ]

    monkeypatch.setattr("docpull.free_core._scan_site", fake_scan)

    output_dir = tmp_path / "map"
    payload = map_url(
        "https://docs.example.com",
        output_dir=output_dir,
        policy=PolicyConfig(),
        max_results=10,
    )

    assert payload["workflow"] == "map"
    assert (output_dir / "candidate_sources.ndjson").exists()
    assert (output_dir / "selected_sources.ndjson").exists()
    assert (output_dir / "selected_urls.txt").exists()
    assert "sponsor.example.net" not in (output_dir / "selected_urls.txt").read_text(encoding="utf-8")
    assert (output_dir / "sitegraph.json").exists()
    assert (output_dir / "MAP.md").exists()


def test_crawl_cli_accepts_max_pages_alias(tmp_path: Path, monkeypatch) -> None:
    called: dict[str, object] = {}

    def fake_crawl_url(*args, **kwargs):
        called["max_results"] = kwargs.get("max_results")
        called["max_depth"] = kwargs.get("max_depth")
        called["mode"] = kwargs.get("mode")
        called["render"] = kwargs.get("render")
        called["audit_gaps"] = kwargs.get("audit_gaps")
        called["include_locales"] = kwargs.get("include_locales")
        return {"workflow": "crawl", "output_dir": str(tmp_path / "crawl"), "summary": {}}

    monkeypatch.setattr("docpull.free_core.crawl_url", fake_crawl_url)

    assert (
        run_crawl_url_cli(
            [
                "https://example.com",
                "--max-pages",
                "5",
                "--mode",
                "exhaustive-docs",
                "--audit-gaps",
                "--include-locales",
                "--render",
                "fallback",
                "-o",
                str(tmp_path / "crawl"),
                "--json",
            ]
        )
        == 0
    )
    assert called["max_results"] == 5
    assert called["max_depth"] == 3
    assert called["mode"] == "exhaustive-docs"
    assert called["render"] == "fallback"
    assert called["audit_gaps"] is True
    assert called["include_locales"] is True


def test_crawl_url_uses_deep_core_discovery_for_pack(tmp_path: Path, monkeypatch) -> None:
    _FakeDeepFetcher.discover_urls = [f"https://docs.example.com/page-{index}" for index in range(1, 121)]
    _FakeDeepFetcher.outcomes = {}
    _FakeDeepFetcher.configs = []
    monkeypatch.setattr("docpull.free_core.Fetcher", _FakeDeepFetcher)

    output_dir = tmp_path / "crawl"
    payload = crawl_url(
        "https://docs.example.com",
        output_dir=output_dir,
        policy=PolicyConfig(),
        selectors=None,
        max_results=100,
    )

    records = [
        json.loads(line)
        for line in (output_dir / "documents.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    coverage = json.loads((output_dir / "coverage.report.json").read_text(encoding="utf-8"))
    routes = json.loads((output_dir / "acquisition.routes.json").read_text(encoding="utf-8"))

    assert payload["workflow"] == "crawl"
    assert len(records) == 100
    assert coverage["summary"]["discovered_url_count"] == 120
    assert coverage["summary"]["selected_url_count"] == 100
    assert coverage["summary"]["extracted_doc_count"] == 100
    assert routes["routes"][0]["route"] == "core_crawl"
    assert routes["routes"][0]["selected_count"] == 100
    assert (output_dir / "sources" / "001.md").exists()
    assert (output_dir / "PACK_AUDIT.md").exists()
    assert (output_dir / "AGENT_CONTEXT.md").exists()


def test_exhaustive_crawl_writes_routes_and_markdown_alternates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _FakeDeepFetcher.discover_urls = ["https://docs.example.com/guide"]
    _FakeDeepFetcher.outcomes = {}
    _FakeDeepFetcher.configs = []

    async def fake_scan(url: str, *, policy: PolicyConfig, source: str, max_results: int):
        if source == "llms":
            return [CandidateSourceRecord(url="https://docs.example.com/llms-page", source="llms")]
        if source == "sitemaps":
            return [CandidateSourceRecord(url="https://docs.example.com/sitemap-page", source="sitemap")]
        return []

    monkeypatch.setattr("docpull.free_core.Fetcher", _FakeDeepFetcher)
    monkeypatch.setattr("docpull.free_core._scan_site_source_records", fake_scan)

    output_dir = tmp_path / "crawl"
    payload = crawl_url(
        "https://docs.example.com",
        output_dir=output_dir,
        policy=PolicyConfig(),
        selectors=None,
        max_results=10,
        mode="exhaustive-docs",
    )

    routes = json.loads((output_dir / "acquisition.routes.json").read_text(encoding="utf-8"))
    route_names = [route["route"] for route in routes["routes"]]
    selected_urls = (output_dir / "selected_urls.txt").read_text(encoding="utf-8")

    assert payload["mode"] == "exhaustive-docs"
    assert route_names[:3] == ["llms_txt", "sitemaps", "docs_nav"]
    assert "core_crawl" in route_names
    assert "markdown_alternates" in route_names
    assert "https://docs.example.com/guide.md" in selected_urls


def test_crawl_coverage_counts_skips_and_failures(tmp_path: Path, monkeypatch) -> None:
    _FakeDeepFetcher.discover_urls = [
        "https://docs.example.com/guide",
        "https://docs.example.com/en/guide",
        "https://docs.example.com/login",
        "https://docs.example.com/file.pdf",
        "https://docs.example.com/js",
        "https://docs.example.com/auth",
        "https://docs.example.com/cf",
        "https://docs.example.com/fail",
        "https://docs.example.com/ok",
    ]
    _FakeDeepFetcher.configs = []
    _FakeDeepFetcher.outcomes = {
        "https://docs.example.com/js": SimpleNamespace(
            error=None,
            should_skip=True,
            skip_reason="JS-only SPA",
            skip_code=SkipReason.JS_ONLY_SPA,
            status_code=200,
            content_type="text/html",
        ),
        "https://docs.example.com/auth": SimpleNamespace(
            error=None,
            should_skip=True,
            skip_reason="HTTP 401",
            skip_code=SkipReason.HTTP_ERROR,
            status_code=401,
            content_type="text/html",
        ),
        "https://docs.example.com/cf": SimpleNamespace(
            error=None,
            should_skip=True,
            skip_reason="Cloudflare challenge",
            skip_code=SkipReason.HTTP_ERROR,
            status_code=403,
            content_type="text/html",
        ),
        "https://docs.example.com/fail": SimpleNamespace(
            error="boom",
            should_skip=False,
            skip_reason=None,
            skip_code=None,
            status_code=None,
            content_type=None,
        ),
    }
    monkeypatch.setattr("docpull.free_core.Fetcher", _FakeDeepFetcher)

    output_dir = tmp_path / "crawl"
    crawl_url(
        "https://docs.example.com",
        output_dir=output_dir,
        policy=PolicyConfig(),
        selectors=None,
        max_results=20,
    )

    coverage = json.loads((output_dir / "coverage.report.json").read_text(encoding="utf-8"))
    summary = coverage["summary"]

    assert coverage["skip_counts"]["localized_duplicate"] == 1
    assert coverage["skip_counts"]["low_value_path"] == 1
    assert coverage["skip_counts"]["binary_or_download"] == 1
    assert summary["skipped_js_only"] == 1
    assert summary["blocked_by_auth"] == 2
    assert summary["blocked_by_cloudflare"] == 1
    assert summary["binary_pdf_skipped"] == 1
    assert summary["nonzero_failures"] == 1


def test_crawl_hygiene_filters_github_drift(tmp_path: Path, monkeypatch) -> None:
    _FakeDeepFetcher.discover_urls = [
        "https://github.com/org/repo",
        "https://github.com/org/repo/blob/main/README.md",
        "https://github.com/features/actions",
        "https://github.com/other/repo/blob/main/docs.md",
    ]
    _FakeDeepFetcher.outcomes = {}
    _FakeDeepFetcher.configs = []
    monkeypatch.setattr("docpull.free_core.Fetcher", _FakeDeepFetcher)

    output_dir = tmp_path / "github"
    crawl_url(
        "https://github.com/org/repo",
        output_dir=output_dir,
        policy=PolicyConfig(),
        selectors=None,
        max_results=10,
    )

    selected_urls = (output_dir / "selected_urls.txt").read_text(encoding="utf-8")
    coverage = json.loads((output_dir / "coverage.report.json").read_text(encoding="utf-8"))

    assert "https://github.com/org/repo/blob/main/README.md" in selected_urls
    assert "https://github.com/features/actions" not in selected_urls
    assert "https://github.com/other/repo" not in selected_urls
    assert coverage["skip_counts"]["github_chrome"] == 1
    assert coverage["skip_counts"]["github_repo_drift"] == 1


def test_crawl_include_locales_keeps_localized_pages(tmp_path: Path, monkeypatch) -> None:
    _FakeDeepFetcher.discover_urls = [
        "https://docs.example.com/guide",
        "https://docs.example.com/en/guide",
    ]
    _FakeDeepFetcher.outcomes = {}
    _FakeDeepFetcher.configs = []
    monkeypatch.setattr("docpull.free_core.Fetcher", _FakeDeepFetcher)

    output_dir = tmp_path / "locales"
    crawl_url(
        "https://docs.example.com",
        output_dir=output_dir,
        policy=PolicyConfig(),
        selectors=None,
        max_results=10,
        include_locales=True,
    )

    selected_urls = (output_dir / "selected_urls.txt").read_text(encoding="utf-8")
    coverage = json.loads((output_dir / "coverage.report.json").read_text(encoding="utf-8"))
    assert "https://docs.example.com/guide" in selected_urls
    assert "https://docs.example.com/en/guide" in selected_urls
    assert "localized_duplicate" not in coverage["skip_counts"]


def test_crawl_render_readiness_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "docpull.free_core.check_render_backend_availability",
        lambda backend: (False, "missing"),
    )

    exit_code = run_crawl_url_cli(
        [
            "https://docs.example.com",
            "--render",
            "fallback",
            "-o",
            str(tmp_path / "crawl"),
        ]
    )

    assert exit_code == 1
    assert "Static crawl can run, but JS-only coverage requires agent-browser" in capsys.readouterr().out


def test_crawl_audit_gaps_returns_nonzero_for_shallow_pack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _FakeDeepFetcher.discover_urls = [f"https://docs.example.com/page-{index}" for index in range(5)]
    _FakeDeepFetcher.outcomes = {}
    _FakeDeepFetcher.configs = []
    monkeypatch.setattr("docpull.free_core.Fetcher", _FakeDeepFetcher)

    exit_code = run_crawl_url_cli(
        [
            "https://docs.example.com",
            "--max-pages",
            "1",
            "--audit-gaps",
            "-o",
            str(tmp_path / "crawl"),
            "--json",
        ]
    )

    result = json.loads((tmp_path / "crawl" / "crawl.result.json").read_text(encoding="utf-8"))
    score = json.loads((tmp_path / "crawl" / "pack.score.json").read_text(encoding="utf-8"))
    assert exit_code == 2
    assert result["status"] == "completed_with_gaps"
    assert "one-document or empty pack" in result["audit_gaps"]
    assert any("appears shallow" in warning for warning in score["warnings"])


def test_crawl_audit_gaps_passes_for_healthy_pack(tmp_path: Path, monkeypatch) -> None:
    _FakeDeepFetcher.discover_urls = [f"https://docs.example.com/page-{index}" for index in range(1, 6)]
    _FakeDeepFetcher.outcomes = {}
    _FakeDeepFetcher.configs = []
    monkeypatch.setattr("docpull.free_core.Fetcher", _FakeDeepFetcher)

    exit_code = run_crawl_url_cli(
        [
            "https://docs.example.com",
            "--max-pages",
            "5",
            "--audit-gaps",
            "-o",
            str(tmp_path / "crawl"),
            "--json",
        ]
    )

    coverage = json.loads((tmp_path / "crawl" / "coverage.report.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert coverage["summary"]["coverage_confidence"] == "high"


def test_scrape_url_writes_standard_pack_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("docpull.parity.Fetcher", _FakeParityFetcher)

    pack_dir = tmp_path / "scrape-pack"
    payload = scrape_url(
        "https://docs.example.com/guide",
        output_dir=pack_dir,
        policy=PolicyConfig(),
    )

    assert payload["workflow"] == "extract-pack"
    for filename in [
        "documents.ndjson",
        "chunks.jsonl",
        "corpus.manifest.json",
        "sources.md",
        "citations.json",
        "pack.score.json",
        "PACK_AUDIT.md",
        "basis.ndjson",
        "context.lock.json",
        "run.accounting.json",
        "AGENT_CONTEXT.md",
    ]:
        assert (pack_dir / filename).exists()


def test_batch_scrape_writes_multi_url_pack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("docpull.parity.Fetcher", _FakeParityFetcher)

    output_dir = tmp_path / "batch"
    payload = batch_scrape(
        ["https://example.com/pricing", "https://example.com/product"],
        input_path=None,
        output_dir=output_dir,
        policy=PolicyConfig(),
    )

    assert payload["input_url_count"] == 2
    assert payload["summary"]["record_count"] == 2
    assert (output_dir / "AGENT_CONTEXT.md").exists()
    assert (output_dir / "documents.ndjson").exists()


def test_redaction_scans_and_writes_copy(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(
        pack_dir,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://docs.example.com/secret",
                "title": "Secret",
                "content": "Email dev@example.com and token = abcdefghijklmnopqrstuvwxyz.",
                "content_hash": "hash_1",
                "source_type": "test",
            }
        ],
    )

    scan = scan_sensitive_content(pack_dir)
    report = redact_pack(pack_dir, policy_path=None, output_dir=tmp_path / "redacted")

    assert scan["finding_count"] >= 1
    assert report["finding_count"] >= 1
    assert "dev@example.com" in (pack_dir / "documents.ndjson").read_text(encoding="utf-8")
    assert "dev@example.com" not in (tmp_path / "redacted" / "documents.ndjson").read_text(encoding="utf-8")
    assert (tmp_path / "redacted" / "redaction.report.json").exists()


def test_redaction_scan_normalizes_pack_path(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(
        pack_dir,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://docs.example.com/secret",
                "title": "Secret",
                "content": "Email dev@example.com.",
                "content_hash": "hash_1",
                "source_type": "test",
            }
        ],
    )

    payload = scan_sensitive_content(pack_dir / ".." / "pack")

    assert payload["finding_count"] >= 1
    assert payload["findings"][0]["path"]


def test_redaction_accepts_full_source_policy_file(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(
        pack_dir,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://docs.example.com/secret",
                "title": "Secret",
                "content": "Email dev@example.com.",
                "content_hash": "hash_1",
                "source_type": "test",
            }
        ],
    )
    policy_path = tmp_path / "docpull.policy.yml"
    policy_path.write_text(
        """
schema_version: 1
allowed_domains:
  - docs.example.com
redaction:
  enabled: true
  backend: regex
  patterns:
    - name: email
      regex: "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+"
""",
        encoding="utf-8",
    )

    payload = scan_sensitive_content(pack_dir, policy_path=policy_path)

    assert payload["backend"] == "regex"
    assert payload["findings"][0]["matches"] == {"email": 1}


def test_redaction_presidio_backend_uses_optional_analyzer(tmp_path: Path, monkeypatch) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(
        pack_dir,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://docs.example.com/support",
                "title": "Support",
                "content": "Contact Ada Lovelace at ada@example.com for access.",
                "content_hash": "hash_1",
                "source_type": "test",
            }
        ],
    )
    policy_path = tmp_path / "redaction.yml"
    policy_path.write_text(
        """
schema_version: 1
enabled: true
backend: presidio
language: en
entities:
  - EMAIL_ADDRESS
score_threshold: 0.8
patterns: []
""",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []
    init_calls: list[dict[str, object]] = []

    class FakeAnalyzer:
        def __init__(self, **kwargs: object) -> None:
            init_calls.append(kwargs)

        def analyze(self, **kwargs: object) -> list[object]:
            calls.append(kwargs)
            text = str(kwargs["text"])
            if "ada@example.com" not in text:
                return []
            start = text.index("ada@example.com")
            end = start + len("ada@example.com")
            return [
                SimpleNamespace(
                    entity_type="EMAIL_ADDRESS",
                    start=start,
                    end=end,
                    score=0.95,
                ),
                SimpleNamespace(
                    entity_type="PERSON",
                    start=text.index("Ada"),
                    end=text.index("Ada") + len("Ada Lovelace"),
                    score=0.2,
                ),
            ]

    class FakeNlpEngine:
        pass

    class FakeNlpArtifacts:
        def __init__(self, *args: object) -> None:
            self.args = args

    monkeypatch.setitem(sys.modules, "presidio_analyzer", SimpleNamespace(AnalyzerEngine=FakeAnalyzer))
    monkeypatch.setitem(
        sys.modules,
        "presidio_analyzer.nlp_engine",
        SimpleNamespace(NlpEngine=FakeNlpEngine, NlpArtifacts=FakeNlpArtifacts),
    )

    scan = scan_sensitive_content(pack_dir, policy_path=policy_path)
    report = redact_pack(
        pack_dir,
        policy_path=policy_path,
        output_dir=tmp_path / "redacted-presidio",
    )

    assert scan["backend"] == "presidio"
    assert scan["findings"][0]["matches"] == {"presidio:EMAIL_ADDRESS": 1}
    assert init_calls[0]["nlp_engine"] is not None
    assert init_calls[0]["supported_languages"] == ["en"]
    assert calls[0]["language"] == "en"
    assert calls[0]["entities"] == ["EMAIL_ADDRESS"]
    redacted_text = (tmp_path / "redacted-presidio" / "documents.ndjson").read_text(encoding="utf-8")
    assert "ada@example.com" not in redacted_text
    assert "[REDACTED:EMAIL_ADDRESS]" in redacted_text
    assert report["match_count"] >= 1


def test_policy_redaction_init_cli(tmp_path: Path) -> None:
    path = tmp_path / "redaction.yml"

    assert main(["policy", "redaction", "init", "--output", str(path)]) == 0

    payload = write_default_redaction_policy(tmp_path / "direct.yml")
    assert path.exists()
    assert payload["policy"]["enabled"] is True


def test_pack_publish_and_redact_cli(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)
    (pack_dir / "sources" / "secret.md").write_text("token = abcdefghijklmnopqrstuvwxyz", encoding="utf-8")

    assert main(["pack", "publish", str(pack_dir), "--target", "agent-docs"]) == 0
    assert main(["pack", "audit", str(pack_dir), "--redaction", "--json"]) == 0
    assert main(["pack", "redact", str(pack_dir), "-o", str(tmp_path / "redacted")]) == 0

    assert (pack_dir / "AGENT_CONTEXT.md").exists()
    report = json.loads((tmp_path / "redacted" / "redaction.report.json").read_text(encoding="utf-8"))
    assert report["finding_count"] >= 1


def test_pack_audit_redaction_json_stays_inside_pack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)
    (pack_dir / "sources" / "secret.md").write_text("token = abcdefghijklmnopqrstuvwxyz", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["pack", "audit", str(pack_dir), "--redaction", "--json"]) == 0

    audit_path = pack_dir / "pack.audit.json"
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit_path.exists()
    assert payload["artifacts"]["json"] == "pack.audit.json"
    assert payload["redaction"]["finding_count"] >= 1
    assert not (tmp_path / "pack.audit.json").exists()


def test_context_asset_domains_allow_www_static_sibling() -> None:
    domains = asset_allowed_domains_for_domain("www.djangoproject.com")

    assert "www.djangoproject.com" in domains
    assert "djangoproject.com" in domains
    assert allowed_by_domains("https://static.djangoproject.com/logo.png", domains)


def test_images_cli_extracts_pack_image_candidates(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(
        pack_dir,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://docs.example.com/brand",
                "title": "Brand",
                "content": "Logo: ![Logo](/assets/logo.png)",
                "content_hash": "hash_1",
                "source_type": "test",
            }
        ],
    )

    output_dir = tmp_path / "images"
    assert run_images_cli([str(pack_dir), "-o", str(output_dir), "--no-download-assets"]) == 0

    result = json.loads((output_dir / "image.result.json").read_text(encoding="utf-8"))
    assert result["summary"]["asset_count"] == 1
    assert (output_dir / "images.ndjson").exists()
    assert (output_dir / "run.accounting.json").exists()


def test_answer_entities_and_brief_top_level_cli_use_existing_pack(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(
        pack_dir,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://example.com/product",
                "title": "Example Product",
                "content": (
                    "Example Product pricing is $10. Contact team@example.com. "
                    "The API supports OAuth token authentication."
                ),
                "content_hash": "hash_1",
                "source_type": "test",
            }
        ],
    )

    answer_dir = tmp_path / "answer"
    entities_dir = tmp_path / "entities"
    brief_dir = tmp_path / "brief"

    assert (
        run_answer_top_cli(
            [
                str(pack_dir),
                "--question",
                "What is the pricing?",
                "-o",
                str(answer_dir),
            ]
        )
        == 0
    )
    assert run_entities_top_cli([str(pack_dir), "-o", str(entities_dir)]) == 0
    assert (
        run_brief_cli(
            [
                str(pack_dir),
                "--objective",
                "Summarize product pricing",
                "-o",
                str(brief_dir),
            ]
        )
        == 0
    )

    assert (answer_dir / "ANSWER.md").exists()
    assert (answer_dir / "basis.ndjson").exists()
    answer_basis = json.loads((answer_dir / "basis.ndjson").read_text(encoding="utf-8").splitlines()[0])
    assert answer_basis["schema_version"] == 2
    assert answer_basis["evidence_state"] in {"supported", "partial", "insufficient"}
    assert (entities_dir / "entities.result.json").exists()
    assert (entities_dir / "basis.ndjson").exists()
    assert (brief_dir / "RESEARCH_BRIEF.md").exists()
    assert (brief_dir / "basis.ndjson").exists()


def test_brief_cli_uses_brief_scale_graph_limit(tmp_path: Path, monkeypatch) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)
    output_dir = tmp_path / "brief"
    captured: dict[str, object] = {}

    def fake_prepare_pack(*args, **kwargs):
        captured["graph_entity_limit"] = kwargs.get("graph_entity_limit")
        return {"schema_version": 1, "artifacts": {}}

    monkeypatch.setattr("docpull.free_core.prepare_pack", fake_prepare_pack)

    assert run_brief_cli([str(pack_dir), "-o", str(output_dir), "--json"]) == 0

    assert captured["graph_entity_limit"] == DEFAULT_BRIEF_ENTITY_LIMIT
    assert (output_dir / "basis.ndjson").exists()


def test_free_core_smoke_dry_run_writes_acceptance_plan(tmp_path: Path) -> None:
    output_dir = tmp_path / "smoke"

    assert run_free_core_smoke_cli(["--dry-run", "-o", str(output_dir), "--json"]) == 0

    payload = json.loads((output_dir / "free-core-smoke.result.json").read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["summary"]["planned_count"] >= 10
    assert payload["summary"]["failed_count"] == 0
    assert any(case["name"] == "product-pricing" for case in payload["cases"])
    assert (output_dir / "FREE_CORE_SMOKE.md").exists()
    assert (output_dir / "free-core-smoke.cases.ndjson").exists()


def test_free_core_smoke_single_case_includes_prerequisite(tmp_path: Path) -> None:
    output_dir = tmp_path / "smoke"

    assert (
        run_free_core_smoke_cli(
            [
                "--dry-run",
                "--case",
                "docs-search",
                "-o",
                str(output_dir),
            ]
        )
        == 0
    )

    payload = json.loads((output_dir / "free-core-smoke.result.json").read_text(encoding="utf-8"))
    assert [case["name"] for case in payload["cases"]] == ["docs-scrape", "docs-search"]


def test_free_core_smoke_deep_dry_run_adds_deep_cases(tmp_path: Path) -> None:
    output_dir = tmp_path / "smoke"

    assert run_free_core_smoke_cli(["--dry-run", "--deep", "-o", str(output_dir), "--json"]) == 0

    payload = json.loads((output_dir / "free-core-smoke.result.json").read_text(encoding="utf-8"))
    names = {case["name"] for case in payload["cases"]}
    assert payload["suite"] == "deep"
    assert "recursive-docs-crawl" in names
    assert "schema-product-fields" in names


def test_monitor_change_classifier_detects_free_categories(tmp_path: Path) -> None:
    old_pack = tmp_path / "old"
    new_pack = tmp_path / "new"
    write_context_pack(
        old_pack,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://docs.example.com/api",
                "title": "API",
                "content": "The API accepts a token parameter. Pricing is $1.",
                "content_hash": "old_hash",
                "source_type": "test",
            }
        ],
    )
    write_context_pack(
        new_pack,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://docs.example.com/api",
                "title": "API",
                "content": "The API endpoint accepts an OAuth token parameter. Pricing is $2.",
                "content_hash": "new_hash",
                "source_type": "test",
            }
        ],
    )

    payload = classify_pack_changes(old_pack, new_pack)
    categories = set(payload["by_type"])

    assert {"pricing_billing", "auth_security", "parameter_schema", "api_behavior"} <= categories
    assert payload["classification_count"] >= 4
    assert payload["evals"]

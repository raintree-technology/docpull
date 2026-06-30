"""Tests for persistent DocPull project workflows."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from docpull import project as project_module
from docpull.cli import main
from docpull.models.events import EventType
from docpull.project import (
    ProjectError,
    add_source,
    diff_project,
    export_context_pack,
    generate_eval_set,
    init_project,
    plan_project,
    project_history,
    project_status,
    release_context_pack,
    remote_login,
    review_project_run,
    sync_project,
    watch_project,
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _install_fake_fetcher(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> type:
    class FakeFetcher:
        calls = 0

        def __init__(self, config: Any) -> None:
            self.config = config
            self.stats = SimpleNamespace(pages_fetched=1, pages_failed=0, pages_skipped=0)

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def run(self):  # type: ignore[no-untyped-def]
            index = min(FakeFetcher.calls, len(contents) - 1)
            content = contents[index]
            FakeFetcher.calls += 1
            output_dir = self.config.output.directory
            output_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "document_id": f"doc_{index}",
                "url": self.config.url,
                "title": "API Pricing",
                "content": content,
                "content_hash": _hash(content),
                "source_type": "test",
                "fetched_at": "2026-06-24T00:00:00+00:00",
                "metadata": {},
                "extraction": {},
            }
            (output_dir / "documents.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")
            yield SimpleNamespace(type=EventType.STARTED, message="started")
            yield SimpleNamespace(type=EventType.COMPLETED, message="done")

    monkeypatch.setattr(project_module, "Fetcher", FakeFetcher)
    return FakeFetcher


def test_init_creates_project_dirs_and_index_without_overwrite(tmp_path: Path) -> None:
    payload = init_project(name="Stripe Docs", source="https://docs.stripe.com", root=tmp_path)

    assert payload["name"] == "stripe-docs"
    assert (tmp_path / "docpull.yaml").exists()
    assert (tmp_path / ".docpull" / "runs").is_dir()
    assert (tmp_path / ".docpull" / "cache").is_dir()
    assert (tmp_path / ".docpull" / "manifests").is_dir()
    assert (tmp_path / ".docpull" / "exports").is_dir()
    assert (tmp_path / ".docpull" / "evals").is_dir()
    assert (tmp_path / ".docpull" / "releases").is_dir()

    conn = sqlite3.connect(tmp_path / ".docpull" / "index.sqlite")
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        source_count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    finally:
        conn.close()
    assert user_version == 3
    assert source_count == 1

    with pytest.raises(ProjectError, match="already exists"):
        init_project(name="again", root=tmp_path)

    forced = init_project(name="again", root=tmp_path, force=True)
    assert forced["name"] == "again"
    conn = sqlite3.connect(tmp_path / ".docpull" / "index.sqlite")
    try:
        source_count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    finally:
        conn.close()
    assert source_count == 0


def test_add_normalizes_sources_and_rejects_duplicates(tmp_path: Path) -> None:
    init_project(name="demo", root=tmp_path)

    added = add_source("https://docs.example.com/api", name="Docs API", source_type="openapi", root=tmp_path)

    assert added["source"]["name"] == "docs-api"
    data = yaml.safe_load((tmp_path / "docpull.yaml").read_text(encoding="utf-8"))
    assert data["sources"][0]["type"] == "openapi"

    with pytest.raises(ProjectError, match="Source name already exists"):
        add_source("https://docs2.example.com", name="Docs API", root=tmp_path)
    with pytest.raises(ProjectError, match="Source URL already exists"):
        add_source("https://docs.example.com/api", root=tmp_path)


def test_project_source_rejects_embedded_url_credentials(tmp_path: Path) -> None:
    init_project(name="demo", root=tmp_path)

    with pytest.raises(ProjectError, match="embedded credentials"):
        add_source("https://user:pass@docs.example.com/api", root=tmp_path)


def test_project_env_auth_masks_artifacts_and_resolves_at_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFetcher:
        captured_auth: str | None = None

        def __init__(self, config: Any) -> None:
            self.config = config
            self.stats = SimpleNamespace(pages_fetched=1, pages_failed=0, pages_skipped=0)
            FakeFetcher.captured_auth = config.auth.token

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def run(self):  # type: ignore[no-untyped-def]
            output_dir = self.config.output.directory
            output_dir.mkdir(parents=True, exist_ok=True)
            content = "Private API context."
            record = {
                "document_id": "doc_private",
                "url": self.config.url,
                "title": "Private API",
                "content": content,
                "content_hash": _hash(content),
                "source_type": "test",
                "metadata": {},
                "extraction": {},
            }
            (output_dir / "documents.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")
            yield SimpleNamespace(type=EventType.STARTED, message="started")
            yield SimpleNamespace(type=EventType.COMPLETED, message="done")

    monkeypatch.setattr(project_module, "Fetcher", FakeFetcher)
    init_project(name="demo", root=tmp_path)
    add_source(
        "https://docs.example.com/private",
        name="private",
        auth={"type": "bearer_env", "env": "PRIVATE_DOCS_TOKEN", "policy": "explicit-private"},
        root=tmp_path,
    )

    with pytest.raises(ProjectError, match="PRIVATE_DOCS_TOKEN"):
        sync_project(root=tmp_path, run_id="run_missing_auth")

    monkeypatch.setenv("PRIVATE_DOCS_TOKEN", "secret-token-value")
    sync_project(root=tmp_path, run_id="run_auth")

    assert FakeFetcher.captured_auth == "secret-token-value"
    manifest_text = (tmp_path / ".docpull" / "runs" / "run_auth" / "manifest.json").read_text(
        encoding="utf-8"
    )
    status = project_status(root=tmp_path)

    assert "secret-token-value" not in manifest_text
    assert "PRIVATE_DOCS_TOKEN" not in manifest_text
    assert status["sources"][0]["auth"] == {
        "source_name": "private",
        "type": "bearer_env",
        "policy": "explicit-private",
        "ready": True,
        "credential": "[env]",
    }


def test_sync_writes_run_artifacts_and_indexes_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fetcher(monkeypatch, ["API pricing is $10 and retry behavior is optional."])
    init_project(name="demo", root=tmp_path)
    add_source("https://docs.example.com/api", name="docs", root=tmp_path)

    payload = sync_project(root=tmp_path, run_id="run_a")
    run_dir = tmp_path / ".docpull" / "runs" / "run_a"

    assert payload["summary"]["document_count"] == 1
    assert payload["summary"]["chunk_count"] >= 1
    for name in (
        "run.json",
        "documents.jsonl",
        "chunks.jsonl",
        "manifest.json",
        "errors.jsonl",
        "accounting.json",
        "source-health.json",
        "documents.ndjson",
        "corpus.manifest.json",
        "sources.md",
        "local.pack.json",
    ):
        assert (run_dir / name).exists(), name

    record = _jsonl(run_dir / "documents.jsonl")[0]
    assert record["metadata"]["docpull_project_source"] == "docs"
    assert record["canonical_url"] == "https://docs.example.com/api"
    assert record["license_hint"] is None
    assert (run_dir / record["text_path"]).exists()

    conn = sqlite3.connect(tmp_path / ".docpull" / "index.sqlite")
    try:
        run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        indexed_doc = conn.execute("SELECT canonical_url, license_hint FROM documents").fetchone()
    finally:
        conn.close()
    assert run_count == 1
    assert doc_count == 1
    assert chunk_count >= 1
    assert indexed_doc == ("https://docs.example.com/api", None)


def test_sync_cleans_titles_and_dedupes_host_alias_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFetcher:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.stats = SimpleNamespace(pages_fetched=2, pages_failed=0, pages_skipped=0)

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def run(self):  # type: ignore[no-untyped-def]
            output_dir = self.config.output.directory
            output_dir.mkdir(parents=True, exist_ok=True)
            records = [
                {
                    "document_id": "doc_alias",
                    "url": "https://example.com/docs",
                    "title": "Overview<!-- --> | Docs",
                    "content": "# Welcome\nSee https://example.com/docs/quickstart for setup.",
                    "content_hash": _hash("# Welcome\nSee https://example.com/docs/quickstart for setup."),
                    "source_type": "test",
                    "metadata": {},
                    "extraction": {},
                },
                {
                    "document_id": "doc_source",
                    "url": "https://docs.example.com",
                    "title": "Overview<!-- --> | Docs",
                    "content": "# Welcome\nSee https://docs.example.com/docs/quickstart for setup.",
                    "content_hash": _hash(
                        "# Welcome\nSee https://docs.example.com/docs/quickstart for setup."
                    ),
                    "source_type": "test",
                    "metadata": {},
                    "extraction": {},
                },
            ]
            (output_dir / "documents.ndjson").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            yield SimpleNamespace(type=EventType.STARTED, message="started")
            yield SimpleNamespace(type=EventType.COMPLETED, message="done")

    monkeypatch.setattr(project_module, "Fetcher", FakeFetcher)
    init_project(name="demo", root=tmp_path)
    add_source("https://docs.example.com", name="docs", root=tmp_path)

    payload = sync_project(root=tmp_path, run_id="run_alias")
    run_dir = tmp_path / ".docpull" / "runs" / "run_alias"
    records = _jsonl(run_dir / "documents.jsonl")
    health = json.loads((run_dir / "source-health.json").read_text(encoding="utf-8"))

    assert payload["summary"]["document_count"] == 1
    assert records[0]["url"] == "https://docs.example.com"
    assert records[0]["title"] == "Overview | Docs"
    assert health["sources"][0]["document_count"] == 1


def test_discovery_refresh_updates_config_and_syncs_exact_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFetcher:
        discover_calls = 0
        fetched_urls: list[str] = []

        def __init__(self, config: Any) -> None:
            self.config = config
            self.stats = SimpleNamespace(pages_fetched=0, pages_failed=0, pages_skipped=0)

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def discover(self) -> list[str]:
            FakeFetcher.discover_calls += 1
            return [
                "https://docs.example.com/api/a",
                "https://docs.example.com/api/b",
                "https://docs.example.com/api/a",
            ]

        async def fetch_one(self, url: str, *, save: bool = True) -> SimpleNamespace:
            FakeFetcher.fetched_urls.append(url)
            self.stats.pages_fetched += 1
            if save:
                output_dir = self.config.output.directory
                output_dir.mkdir(parents=True, exist_ok=True)
                content = f"Fetched {url}"
                record = {
                    "document_id": f"doc_{self.stats.pages_fetched}",
                    "url": url,
                    "title": url.rsplit("/", 1)[-1],
                    "content": content,
                    "content_hash": _hash(content),
                    "source_type": "test",
                    "metadata": {},
                    "extraction": {},
                }
                with (output_dir / "documents.ndjson").open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(record) + "\n")
            return SimpleNamespace(error=None, should_skip=False, skip_code=None, skip_reason=None)

    monkeypatch.setattr(project_module, "Fetcher", FakeFetcher)
    init_project(name="demo", root=tmp_path)
    added = add_source("https://docs.example.com/api", name="docs", discover=True, root=tmp_path)

    assert added["source"]["discover"] is True
    assert added["source"]["refresh_discovery_on_sync"] is False
    assert added["source"]["discovered_urls"] == [
        "https://docs.example.com/api/a",
        "https://docs.example.com/api/b",
    ]

    payload = sync_project(root=tmp_path, run_id="run_discovered")
    run_dir = tmp_path / ".docpull" / "runs" / "run_discovered"
    urls = [record["url"] for record in _jsonl(run_dir / "documents.jsonl")]

    assert payload["summary"]["document_count"] == 3
    assert urls == [
        "https://docs.example.com/api",
        "https://docs.example.com/api/a",
        "https://docs.example.com/api/b",
    ]
    assert FakeFetcher.discover_calls == 1
    health = json.loads((run_dir / "source-health.json").read_text(encoding="utf-8"))
    assert health["sources"][0]["discovered_url_count"] == 2

    sync_project(root=tmp_path, run_id="run_refresh", update_discovery=True)
    assert FakeFetcher.discover_calls == 2


def test_project_plan_filters_profiles_and_sync_uses_latest_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_project(name="demo", source="https://exa.ai/docs", root=tmp_path)
    config = project_module.load_project_config(tmp_path)
    source = config.sources[0].model_copy(
        update={
            "discover": True,
            "discovered_urls": [
                "https://exa.ai/docs/api-reference/search",
                "https://api.exa.ai/openapi.json",
                "https://app.stainless.com/api/spec/documented/exa.ai/openapi.documented.yml",
                "https://exa.ai/openapi.json",
                "https://exa.ai/llms.txt",
                "https://developer.monday.com/api-reference/reference/webhooks",
                "https://exa.ai/blog/product",
                "https://exa.ai/es/docs/api-reference/search",
                "https://exa.ai/websets/directory/company/foo",
                "https://exa.ai/legal/terms.pdf",
            ],
            "discovered_at": "2026-06-29T00:00:00+00:00",
        }
    )
    project_module.save_project_config(tmp_path, config.model_copy(update={"sources": [source]}))

    payload = plan_project(
        root=tmp_path,
        plan_id="plan_api",
        profile="api-docs",
        scan_site_hints=False,
        max_pages_per_source=10,
    )

    selected_urls = {item["url"] for item in payload["selected"]}
    rejected_urls = {item["url"] for item in payload["rejected"]}
    plan_dir = tmp_path / ".docpull" / "plans" / "plan_api"

    assert "https://exa.ai/docs/api-reference/search" in selected_urls
    assert "https://api.exa.ai/openapi.json" in selected_urls
    assert "https://app.stainless.com/api/spec/documented/exa.ai/openapi.documented.yml" in selected_urls
    assert "https://exa.ai/openapi.json" in selected_urls
    assert "https://exa.ai/llms.txt" in selected_urls
    assert "https://developer.monday.com/api-reference/reference/webhooks" in rejected_urls
    assert "https://exa.ai/websets/directory/company/foo" in rejected_urls
    assert "https://exa.ai/es/docs/api-reference/search" in rejected_urls
    assert "https://exa.ai/blog/product" in rejected_urls
    assert (plan_dir / "frontier.plan.json").exists()
    assert (plan_dir / "selected_urls.txt").exists()
    assert (plan_dir / "rejected_sources.ndjson").exists()
    assert (tmp_path / ".docpull" / "plans" / "latest-plan").read_text(encoding="utf-8").strip() == "plan_api"

    class FakeFetcher:
        fetched_urls: list[str] = []

        def __init__(self, config: Any) -> None:
            self.config = config
            self.stats = SimpleNamespace(pages_fetched=0, pages_failed=0, pages_skipped=0)

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def fetch_one(self, url: str, *, save: bool = True) -> SimpleNamespace:
            FakeFetcher.fetched_urls.append(url)
            self.stats.pages_fetched += 1
            if save:
                output_dir = self.config.output.directory
                output_dir.mkdir(parents=True, exist_ok=True)
                content = f"Fetched {url}"
                record = {
                    "document_id": f"doc_{self.stats.pages_fetched}",
                    "url": url,
                    "title": url,
                    "content": content,
                    "content_hash": _hash(content),
                    "source_type": "test",
                    "metadata": {},
                    "extraction": {},
                }
                with (output_dir / "documents.ndjson").open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(record) + "\n")
            return SimpleNamespace(error=None, should_skip=False, skip_code=None, skip_reason=None)

    monkeypatch.setattr(project_module, "Fetcher", FakeFetcher)
    sync_payload = sync_project(root=tmp_path, run_id="run_plan", plan="latest")
    run_dir = tmp_path / ".docpull" / "runs" / "run_plan"

    assert sync_payload["plan"]["plan_id"] == "plan_api"
    assert set(FakeFetcher.fetched_urls) == selected_urls
    assert "https://exa.ai/websets/directory/company/foo" not in FakeFetcher.fetched_urls
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["plan"]["plan_id"] == "plan_api"


def test_project_plan_dedupes_markdown_mirror_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_project(name="demo", source="https://docs.example.com", root=tmp_path)
    config = project_module.load_project_config(tmp_path)
    source = config.sources[0].model_copy(
        update={
            "discovered_urls": ["https://docs.example.com/documentation/about"],
            "discovered_at": "2026-06-29T00:00:00+00:00",
        }
    )
    project_module.save_project_config(tmp_path, config.model_copy(update={"sources": [source]}))

    async def fake_scan(**_kwargs: Any) -> list[Any]:
        return [
            project_module.CandidateSourceRecord(
                url="https://docs.example.com/documentation/about.md",
                source="local-site-scan:llms",
                provider="local",
                rank=1,
                metadata={"candidate_origin": "site_scan"},
            )
        ]

    monkeypatch.setattr(project_module, "_scan_project_source_hints", fake_scan)

    payload = plan_project(
        root=tmp_path,
        plan_id="plan_dedupe",
        profile="api-docs",
        scan_site_hints=True,
        max_pages_per_source=10,
    )

    selected_urls = [item["url"] for item in payload["selected"]]
    assert selected_urls.count("https://docs.example.com/documentation/about") == 1
    assert "https://docs.example.com/documentation/about.md" not in selected_urls


def test_sync_quarantines_repeated_rate_limited_prefixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFetcher:
        fetched_urls: list[str] = []

        def __init__(self, config: Any) -> None:
            self.config = config
            self.stats = SimpleNamespace(pages_fetched=0, pages_failed=0, pages_skipped=0)

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def fetch_one(self, url: str, *, save: bool = True) -> SimpleNamespace:
            FakeFetcher.fetched_urls.append(url)
            if url.endswith("/api/a"):
                self.stats.pages_failed += 1
                return SimpleNamespace(error="HTTP 429 Too Many Requests", should_skip=False)
            self.stats.pages_fetched += 1
            if save:
                output_dir = self.config.output.directory
                output_dir.mkdir(parents=True, exist_ok=True)
                content = f"Fetched {url}"
                record = {
                    "document_id": f"doc_{self.stats.pages_fetched}",
                    "url": url,
                    "title": url,
                    "content": content,
                    "content_hash": _hash(content),
                    "source_type": "test",
                    "metadata": {},
                    "extraction": {},
                }
                with (output_dir / "documents.ndjson").open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(record) + "\n")
            return SimpleNamespace(error=None, should_skip=False, skip_code=None, skip_reason=None)

    monkeypatch.setattr(project_module, "Fetcher", FakeFetcher)
    init_project(name="demo", source="https://docs.example.com/root", root=tmp_path)
    config = project_module.load_project_config(tmp_path)
    source = config.sources[0].model_copy(
        update={
            "discovered_urls": [
                "https://docs.example.com/api/a",
                "https://docs.example.com/api/b",
            ]
        }
    )
    project_module.save_project_config(tmp_path, config.model_copy(update={"sources": [source]}))

    sync_project(root=tmp_path, run_id="run_429")
    skips = _jsonl(tmp_path / ".docpull" / "runs" / "run_429" / "skips.jsonl")

    assert "https://docs.example.com/api/b" not in FakeFetcher.fetched_urls
    assert skips[0]["reason"] == "rate_limited_prefix_quarantine"


def test_diff_status_export_and_eval_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DOCPULL_SEMANTIC_DIFF_MODEL", raising=False)
    _install_fake_fetcher(
        monkeypatch,
        [
            "API pricing is $10 and retry behavior is optional.",
            "API pricing is $20 and a required field changes retry behavior.",
        ],
    )
    init_project(name="demo", root=tmp_path)
    add_source("https://docs.example.com/api/pricing", name="docs", root=tmp_path)
    sync_project(root=tmp_path, run_id="run_a")
    sync_project(root=tmp_path, run_id="run_b")

    diff = diff_project(root=tmp_path)

    assert diff["from_run_id"] == "run_a"
    assert diff["to_run_id"] == "run_b"
    assert diff["summary"]["changed_count"] == 1
    assert diff["summary"]["likely_api_behavior_change_count"] == 1
    assert diff["summary"]["pricing_change_count"] == 1
    assert diff["semantic"]["skipped"] is True

    semantic = diff_project(
        root=tmp_path,
        from_run_id="run_a",
        to_run_id="run_b",
        semantic="on",
        semantic_client=lambda _prompt: json.dumps(
            {
                "summary": "required field changed",
                "likely_behavior_changes": ["field"],
                "risks": [],
            }
        ),
    )
    assert semantic["semantic"]["skipped"] is False
    assert semantic["semantic"]["summary"]["summary"] == "required field changed"

    status = project_status(root=tmp_path)
    assert status["last_run_id"] == "run_b"
    assert status["document_count"] == 1
    assert status["changed_since_previous_run"] == 1

    export = export_context_pack(
        target="openai",
        run_id="run_b",
        output_dir=tmp_path / "export",
        root=tmp_path,
    )
    assert Path(export["output_dir"], "context.md").exists()
    assert Path(export["output_dir"], "sources.json").exists()
    assert Path(export["output_dir"], "chunks.jsonl").exists()
    assert Path(export["output_dir"], "citations.json").exists()
    assert Path(export["output_dir"], "manifest.json").exists()
    assert Path(export["output_dir"], "openai-vector.jsonl").exists()

    eval_payload = generate_eval_set(run_id="run_b", limit=10, root=tmp_path)
    cases = _jsonl(Path(eval_payload["path"]))
    assert eval_payload["case_count"] == 1
    assert cases[0]["kind"] == "changed"
    assert cases[0]["expected_citation_ids"] == ["S1"]


def test_project_run_helpers_reject_traversal_run_ids(tmp_path: Path) -> None:
    init_project(name="demo", root=tmp_path)

    for call in (
        lambda: export_context_pack(target="openai", run_id="../run", root=tmp_path),
        lambda: generate_eval_set(run_id="..", root=tmp_path),
        lambda: review_project_run(run_id="../run", root=tmp_path),
        lambda: release_context_pack(target="cursor", run_id="../run", root=tmp_path),
    ):
        with pytest.raises(ProjectError, match="run ID"):
            call()


def test_history_review_and_release_context_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fetcher(
        monkeypatch,
        [
            "API pricing is $10 and retry behavior is optional.",
            "API pricing is $20 and a required field changes retry behavior.",
        ],
    )
    init_project(name="demo", root=tmp_path)
    add_source("https://docs.example.com/api/pricing", name="docs", root=tmp_path)
    sync_project(root=tmp_path, run_id="run_a")
    sync_project(root=tmp_path, run_id="run_b")
    diff_project(root=tmp_path)

    history = project_history(root=tmp_path)
    review = review_project_run(root=tmp_path, run_id="run_b")
    release = release_context_pack(target="cursor", run_id="run_b", tag="v1", root=tmp_path)

    assert [item["run_id"] for item in history["runs"]] == ["run_b", "run_a"]
    assert review["summary"]["changed_count"] == 1
    assert Path(review["paths"]["json"]).exists()
    assert release["tag"] == "v1"
    assert Path(release["release_dir"], "release.json").exists()
    assert Path(release["release_dir"], "context-pack", "context.md").exists()


def test_remote_login_requires_https_for_bearer_tokens(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="must use HTTPS"):
        remote_login(api_url="http://hosted.example", token="secret", root=tmp_path)

    payload = remote_login(
        api_url="http://127.0.0.1:8080/",
        token="local-secret",
        root=tmp_path,
        allow_insecure_local_http=True,
    )

    assert payload["api_url"] == "http://127.0.0.1:8080"


def test_remote_request_rejects_existing_cleartext_config_before_network() -> None:
    with pytest.raises(ProjectError, match="must use HTTPS"):
        project_module._remote_json_request(
            {"api_url": "http://hosted.example", "token": "secret"},
            "GET",
            "/v1/projects/proj",
            None,
        )


def test_project_cli_and_export_context_pack_preserves_legacy_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fetcher(monkeypatch, ["Agent context for API docs."])
    monkeypatch.chdir(tmp_path)

    assert main(["init", "demo"]) == 0
    assert main(["add", "https://docs.example.com/api", "--name", "docs"]) == 0
    assert main(["sync", "--run-id", "run_a", "--json"]) == 0
    assert main(["status", "--json"]) == 0
    assert main(["export", "context-pack", "--target", "langchain", "-o", str(tmp_path / "ctx")]) == 0
    assert (tmp_path / "ctx" / "langchain.jsonl").exists()

    assert (
        main(
            [
                "export",
                str(tmp_path / ".docpull" / "runs" / "run_a"),
                "--format",
                "dspy-jsonl",
                "-o",
                str(tmp_path / "legacy.jsonl"),
            ]
        )
        == 0
    )
    assert _jsonl(tmp_path / "legacy.jsonl")[0]["document_id"] == "doc_0"


def test_watch_composes_project_sync_diff_and_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_fetcher(monkeypatch, ["Watch context pack content."])

    payload = watch_project(
        "https://docs.example.com/watch",
        export_target="langchain",
        root=tmp_path,
    )

    assert payload["run_id"]
    assert payload["changed"] == 0
    assert Path(payload["export"]["output_dir"], "langchain.jsonl").exists()
    assert (tmp_path / "docpull.yaml").exists()

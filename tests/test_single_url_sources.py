"""Tracking single documents (tax-law PDFs, rate pages) with project sync and monitor.

Every test here is offline: fetchers are stubbed and PDFs are generated locally.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from docpull import local_workflows
from docpull import project as project_module
from docpull.cli import main
from docpull.document_parse import parse_one_document, parse_remote_document_bytes
from docpull.local_workflows import refresh_pack
from docpull.monitor import create_monitor_parser, init_monitor, run_monitor_cli, run_monitor_once
from docpull.project import (
    ProjectError,
    ProjectSource,
    add_source,
    init_project,
    load_project_config,
    sync_project,
)
from tests.pack_fixtures import write_context_pack
from tests.pdf_fixtures import tax_table_pdf

PDF_URL = "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"
HTML_URL = "https://revenue.example.gov/individual/rates"


class RecordingFetcher:
    """Stub fetcher that records every request and refuses to crawl."""

    instances: list[RecordingFetcher] = []
    fail_urls: set[str] = set()

    def __init__(self, config: Any) -> None:
        self.config = config
        self.requested: list[str] = []
        self.stats = SimpleNamespace(pages_fetched=0, pages_failed=0, pages_skipped=0)
        RecordingFetcher.instances.append(self)

    async def __aenter__(self) -> RecordingFetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def run(self):  # type: ignore[no-untyped-def]
        raise AssertionError(f"crawl started for {self.config.url}")
        yield  # pragma: no cover

    async def discover(self) -> list[str]:
        raise AssertionError(f"discovery started for {self.config.url}")

    async def fetch_one(self, url: str, *, save: bool = True) -> SimpleNamespace:
        self.requested.append(url)
        if url in RecordingFetcher.fail_urls:
            self.stats.pages_failed += 1
            return SimpleNamespace(error="HTTP 503", should_skip=False, skip_code=None, skip_reason=None)
        self.stats.pages_fetched += 1
        content = f"Content of {url}"
        if save:
            out = self.config.output.directory
            out.mkdir(parents=True, exist_ok=True)
            record = {
                "document_id": f"doc_{abs(hash(url))}",
                "url": url,
                "title": url.rsplit("/", 1)[-1],
                "content": content,
                "content_hash": str(abs(hash(content))),
                "source_type": "remote_pdf" if url.endswith(".pdf") else "html",
                "metadata": {},
                "extraction": {},
            }
            with (out / "documents.ndjson").open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record) + "\n")
        return SimpleNamespace(
            error=None,
            should_skip=False,
            skip_code=None,
            skip_reason=None,
            markdown=content,
            title=url,
            metadata={},
            extraction_info={},
            source_type="test",
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything in this module tries to reach the network."""
    import socket

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("live network access attempted in an offline test")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def fetcher(monkeypatch: pytest.MonkeyPatch) -> type[RecordingFetcher]:
    RecordingFetcher.instances = []
    RecordingFetcher.fail_urls = set()
    monkeypatch.setattr(project_module, "Fetcher", RecordingFetcher)
    monkeypatch.setattr(local_workflows, "Fetcher", RecordingFetcher)
    return RecordingFetcher


def _requested(fetcher: type[RecordingFetcher]) -> list[str]:
    return [url for instance in fetcher.instances for url in instance.requested]


# --- 1. single-URL sources and per-source caps -------------------------------------------


def test_pdf_source_fetches_exactly_its_url(tmp_path: Path, fetcher: type[RecordingFetcher]) -> None:
    init_project(name="tax", root=tmp_path)
    add_source(PDF_URL, name="rp-25-32", source_type="pdf", root=tmp_path)
    add_source(HTML_URL, name="state-rates", single=True, root=tmp_path)

    payload = sync_project(root=tmp_path, run_id="run_1")

    assert payload["status"] == "success"
    assert _requested(fetcher) == [PDF_URL, HTML_URL]
    assert [instance.config.crawl.max_pages for instance in fetcher.instances] == [1, 1]


def test_pdf_suffix_infers_single_and_explicit_off_crawls(tmp_path: Path) -> None:
    assert ProjectSource(name="a", url=PDF_URL).is_single is True
    assert ProjectSource(name="b", url=HTML_URL).is_single is False
    assert ProjectSource(name="c", url=PDF_URL, type="pdf", single=False).is_single is False


def test_max_pages_is_per_source(tmp_path: Path, fetcher: type[RecordingFetcher]) -> None:
    init_project(name="tax", root=tmp_path)
    add_source("https://docs.example.com/a", name="small", max_pages=2, root=tmp_path)
    add_source("https://docs.example.com/b", name="default", root=tmp_path)
    config = load_project_config(tmp_path)
    small, default = config.sources

    small_config = project_module._fetch_config(tmp_path, config, small, tmp_path / "o1")
    default_config = project_module._fetch_config(tmp_path, config, default, tmp_path / "o2")

    assert small_config.crawl.max_pages == 2
    assert default_config.crawl.max_pages == config.crawl.max_pages == 500


def test_discovered_urls_respect_per_source_cap(tmp_path: Path, fetcher: type[RecordingFetcher]) -> None:
    init_project(name="tax", root=tmp_path)
    add_source("https://docs.example.com/a", name="docs", max_pages=2, root=tmp_path)
    config = load_project_config(tmp_path)
    source = config.sources[0].model_copy(
        update={"discovered_urls": [f"https://docs.example.com/a/{n}" for n in range(5)]}
    )
    project_module.save_project_config(tmp_path, config.model_copy(update={"sources": [source]}))

    sync_project(root=tmp_path, run_id="run_1")

    assert _requested(fetcher) == ["https://docs.example.com/a", "https://docs.example.com/a/0"]


def test_single_source_rejects_discovery(tmp_path: Path) -> None:
    init_project(name="tax", root=tmp_path)
    with pytest.raises(ProjectError, match="single-URL sources cannot use discovery"):
        add_source(HTML_URL, single=True, discover=True, root=tmp_path)


def test_add_cli_persists_fetch_options_and_omits_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    init_project(name="tax", root=tmp_path)
    assert main(["add", PDF_URL, "--type", "pdf", "--remote-document-backend", "pdfplumber"]) == 0
    assert main(["add", HTML_URL, "--single", "--max-pages", "1"]) == 0
    assert main(["add", "https://docs.example.com/guide"]) == 0

    sources = yaml.safe_load((tmp_path / "docpull.yaml").read_text())["sources"]

    assert sources[0]["remote_document_backend"] == "pdfplumber"
    assert "single" not in sources[0]
    assert sources[1]["single"] is True and sources[1]["max_pages"] == 1
    assert not {"single", "max_pages", "remote_documents"} & set(sources[2])


def test_lock_ignores_unset_fetch_options_but_tracks_set_ones(tmp_path: Path) -> None:
    plain = ProjectSource(name="a", url=HTML_URL)
    pinned = ProjectSource(name="b", url=PDF_URL, type="pdf", remote_document_backend="pdfplumber")

    assert "fetch" not in project_module._context_dependency_payload(plain)
    assert project_module._context_dependency_payload(pinned)["fetch"] == {
        "remote_document_backend": "pdfplumber"
    }


def test_fetch_options_rejected_on_typed_sources() -> None:
    with pytest.raises(ValueError, match="crawl sources only"):
        ProjectSource(name="d", url="dataset:https://example.com/data.csv", type="dataset", single=True)


# --- 2. remote PDFs through project config -----------------------------------------------


def test_project_config_wires_remote_documents(tmp_path: Path) -> None:
    init_project(name="tax", root=tmp_path)
    add_source(PDF_URL, name="pdf", source_type="pdf", root=tmp_path)
    add_source(HTML_URL, name="html", root=tmp_path)
    add_source(
        "https://www.irs.gov/pub/irs-pdf/i1040gi.pdf", name="optout", remote_documents="off", root=tmp_path
    )
    config = load_project_config(tmp_path)

    filters = {
        source.name: project_module._fetch_config(
            tmp_path, config, source, tmp_path / source.name
        ).content_filter
        for source in config.sources
    }

    assert filters["pdf"].remote_documents == "pdf"
    assert filters["html"].remote_documents == "off"
    assert filters["optout"].remote_documents == "off"

    crawl = config.crawl.model_copy(
        update={"remote_documents": "pdf", "remote_document_backend": "pdfplumber"}
    )
    project_wide = config.model_copy(update={"crawl": crawl})
    html_filter = project_module._fetch_config(
        tmp_path, project_wide, config.sources[1], tmp_path / "h"
    ).content_filter
    assert (html_filter.remote_documents, html_filter.remote_document_backend) == ("pdf", "pdfplumber")


# --- 3. monitor refresh config and failures ----------------------------------------------


def _pdf_pack(path: Path) -> Path:
    write_context_pack(
        path,
        records=[
            {
                "document_id": "rp",
                "url": PDF_URL,
                "title": "Rev. Proc. 2025-32",
                "content": "old",
                "content_hash": "h_old",
                "source_type": "remote_pdf",
                "extraction": {"parser": "pdfplumber"},
            },
            {
                "document_id": "state",
                "url": HTML_URL,
                "title": "Rates",
                "content": "old",
                "content_hash": "h_state",
                "source_type": "html",
            },
        ],
        include_domains=["www.irs.gov", "revenue.example.gov"],
    )
    return path


def test_refresh_reuses_pack_pdf_settings(tmp_path: Path, fetcher: type[RecordingFetcher]) -> None:
    payload = refresh_pack(_pdf_pack(tmp_path / "pack"), output_dir=tmp_path / "out")

    content_filter = fetcher.instances[0].config.content_filter
    assert (content_filter.remote_documents, content_filter.remote_document_backend) == ("pdf", "pdfplumber")
    assert payload["summary"]["remote_documents"] == "pdf"
    assert set(payload["diff"]["changed_urls"]) == {PDF_URL, HTML_URL}


def test_refresh_explicit_settings_win(tmp_path: Path, fetcher: type[RecordingFetcher]) -> None:
    refresh_pack(_pdf_pack(tmp_path / "pack"), output_dir=tmp_path / "out", remote_documents="off")
    assert fetcher.instances[0].config.content_filter.remote_documents == "off"


def test_monitor_reports_failed_refetch_as_failure(
    tmp_path: Path, fetcher: type[RecordingFetcher], capsys
) -> None:
    pack = _pdf_pack(tmp_path / "pack")
    state_dir = tmp_path / "monitors"
    init_monitor("tax", pack, state_dir=state_dir)
    fetcher.fail_urls = {PDF_URL}

    payload = run_monitor_once("tax", state_dir=state_dir)

    summary = payload["summary"]
    assert summary["status"] == "failed"
    assert summary["failed_count"] == 1
    assert summary["failures"] == [{"url": PDF_URL, "reason": "HTTP 503", "kind": "fetch_failed"}]
    assert PDF_URL not in payload["refresh"]["diff"]["unchanged_urls"]
    # The failed cycle must not become the baseline.
    monitor_config = json.loads((state_dir / "tax" / "monitor.json").read_text())
    assert "baseline_pack_dir" not in monitor_config

    assert run_monitor_cli(["--state-dir", str(state_dir), "trigger", "tax"]) == 1
    assert "failed" in capsys.readouterr().out


def test_monitor_success_exit_zero_and_changed_status(
    tmp_path: Path, fetcher: type[RecordingFetcher]
) -> None:
    state_dir = tmp_path / "monitors"
    init_monitor("tax", _pdf_pack(tmp_path / "pack"), state_dir=state_dir, remote_document_backend="pypdf")

    assert run_monitor_cli(["--state-dir", str(state_dir), "run", "tax", "--once", "--json"]) == 0
    assert fetcher.instances[0].config.content_filter.remote_document_backend == "pypdf"
    report = sorted((state_dir / "tax" / "runs").glob("*/monitor.report.json"))[-1]
    assert json.loads(report.read_text())["summary"]["status"] == "changed"


# --- 4. sync status and exit code agree --------------------------------------------------


def test_partial_sync_reports_partial_and_exits_nonzero(
    tmp_path: Path, fetcher: type[RecordingFetcher], monkeypatch, capsys
) -> None:
    init_project(name="tax", root=tmp_path)
    add_source(PDF_URL, name="pdf", source_type="pdf", root=tmp_path)
    add_source(HTML_URL, name="html", single=True, root=tmp_path)
    fetcher.fail_urls = {HTML_URL}
    monkeypatch.chdir(tmp_path)

    assert main(["sync", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "partial"
    assert payload["complete"] is False
    assert payload["summary"]["failed_count"] == 1

    fetcher.fail_urls = set()
    assert main(["sync", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"


def test_all_failed_sync_reports_failed(
    tmp_path: Path, fetcher: type[RecordingFetcher], monkeypatch, capsys
) -> None:
    init_project(name="tax", root=tmp_path)
    add_source(PDF_URL, name="pdf", source_type="pdf", root=tmp_path)
    fetcher.fail_urls = {PDF_URL}
    monkeypatch.chdir(tmp_path)

    assert main(["sync", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


# --- 5. table-aware PDF backend -----------------------------------------------------------


def test_pdfplumber_backend_keeps_bracket_rows_and_separates_tables(tmp_path: Path) -> None:
    pytest.importorskip("pdfplumber")
    pdf = tmp_path / "rates.pdf"
    pdf.write_bytes(tax_table_pdf())

    parsed = parse_one_document(pdf, backend="pdfplumber", source_url=PDF_URL, title=None)

    content = parsed.content
    assert parsed.backend == "pdfplumber"
    assert parsed.metadata["table_count"] == 2
    assert "| Over $24,800 but not over $100,800 | $2,480 plus 12% of the excess over $24,800 |" in content
    assert "| Over $17,700 but not over $67,450 | $1,770 plus 12% of the excess over $17,700 |" in content
    # Side-by-side tables stay separate: the left table finishes before the right one starts.
    assert content.index("$100,800 |") < content.index("Not over $17,700")
    assert content.index("TABLE 1") < content.index("| If Taxable") < content.index("Section 4.02")
    assert content.count("Not over $24,800") == 1


def test_pypdf_baseline_splits_bracket_rows(tmp_path: Path) -> None:
    """Documents why pdfplumber exists: the default backend breaks these rows."""
    pdf = tmp_path / "rates.pdf"
    pdf.write_bytes(tax_table_pdf())
    content = parse_one_document(pdf, backend="pypdf", source_url=PDF_URL, title=None).content
    assert "Over $24,800 but not over $100,800" not in content


def test_pdfplumber_backend_through_isolated_remote_worker() -> None:
    pytest.importorskip("pdfplumber")
    parsed = parse_remote_document_bytes(
        tax_table_pdf(), source_url=PDF_URL, content_type="application/pdf", backend="pdfplumber"
    )
    assert parsed.backend == "pdfplumber"
    assert "| Not over $17,700 | 10% of the taxable income |" in parsed.content


def test_pdfplumber_rejects_non_pdf(tmp_path: Path) -> None:
    from docpull.document_parse import DocumentParseError

    text = tmp_path / "notes.txt"
    text.write_text("hello")
    with pytest.raises(DocumentParseError, match="only supports PDF"):
        parse_one_document(text, backend="pdfplumber", source_url="https://example.com/n", title=None)


# --- 6. monitor help and Slack webhook ---------------------------------------------------


def _monitor_subcommands() -> list[str]:
    parser = create_monitor_parser()
    action = next(a for a in parser._actions if a.dest == "command")
    return list(action.choices)


@pytest.mark.parametrize("sub", _monitor_subcommands())
def test_monitor_subcommand_help_is_subcommand_help(sub: str, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["monitor", sub, "--help"])
    assert exit_info.value.code == 0
    assert f"usage: docpull monitor {sub}" in capsys.readouterr().out


def test_help_command_routes_to_subcommand_help(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["help", "monitor", "run"])
    assert "usage: docpull monitor run" in capsys.readouterr().out


def test_slack_webhook_posts_summary_without_leaking_url(
    tmp_path: Path, fetcher: type[RecordingFetcher]
) -> None:
    state_dir = tmp_path / "monitors"
    init_monitor("tax", _pdf_pack(tmp_path / "pack"), state_dir=state_dir)
    webhook = "https://hooks.slack.com/services/T000/B000/secretsecret"
    posts: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, body: dict[str, Any]) -> int:
        posts.append((url, body))
        return 200

    payload = run_monitor_once("tax", state_dir=state_dir, slack_webhook=webhook, slack_post=post)

    assert payload["notification_outputs"]["slack"] == {"status": "sent", "http_status": 200}
    assert posts[0][0] == webhook
    assert "changed=2" in posts[0][1]["text"] and PDF_URL in posts[0][1]["text"]
    assert payload["summary"]["slack_webhook_supplied"] is True
    run_dir = Path(payload["run_dir"])
    for artifact in run_dir.rglob("*"):
        if artifact.is_file():
            assert "secretsecret" not in artifact.read_text(encoding="utf-8", errors="ignore")
    assert "secretsecret" not in (state_dir / "tax" / "monitor.json").read_text()

    # Same changes again are deduplicated against the new baseline: nothing to send.
    again = run_monitor_once("tax", state_dir=state_dir, slack_webhook=webhook, slack_post=post)
    assert again["notification_outputs"]["slack"]["status"] == "skipped"
    assert len(posts) == 1


def test_slack_webhook_reports_post_errors(tmp_path: Path, fetcher: type[RecordingFetcher]) -> None:
    state_dir = tmp_path / "monitors"
    init_monitor("tax", _pdf_pack(tmp_path / "pack"), state_dir=state_dir)

    def post(_url: str, _body: dict[str, Any]) -> int:
        raise OSError("connection refused")

    payload = run_monitor_once(
        "tax", state_dir=state_dir, slack_webhook="https://hooks.slack.com/services/x", slack_post=post
    )
    assert payload["notification_outputs"]["slack"]["status"] == "error"


def test_slack_webhook_requires_https(tmp_path: Path) -> None:
    state_dir = tmp_path / "monitors"
    init_monitor("tax", _pdf_pack(tmp_path / "pack"), state_dir=state_dir)
    assert (
        run_monitor_cli(["--state-dir", str(state_dir), "trigger", "tax", "--slack-webhook", "http://x"]) == 1
    )

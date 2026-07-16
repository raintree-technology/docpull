"""Tests for typed knowledge-lane context packs."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from docpull.cli import main
from docpull.context_packs.dataset import build_dataset_pack
from docpull.context_packs.feed import build_feed_pack
from docpull.context_packs.openapi import build_openapi_pack
from docpull.context_packs.package import build_package_pack
from docpull.context_packs.paper import build_paper_pack
from docpull.context_packs.repo import build_repo_pack
from docpull.context_packs.standards import build_standards_pack
from docpull.context_packs.transcript import build_transcript_pack
from docpull.context_packs.typed import RemoteText, read_https_text, typed_http_cache
from docpull.context_packs.wiki import build_wiki_pack
from docpull.document_parse import ParsedDocument
from docpull.exports import export_pack
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack
from docpull.pack_tools import prepare_pack


def _remote_json(url: str, payload: Any) -> RemoteText:
    return RemoteText(text=json.dumps(payload), url=url, content_type="application/json", status_code=200)


def _remote_text(url: str, text: str, *, content_type: str = "text/plain") -> RemoteText:
    return RemoteText(text=text, url=url, content_type=content_type, status_code=200)


def _assert_pack_roundtrip(pack_dir: Path, *, pack_file: str, expected_source_types: set[str]) -> None:
    assert (pack_dir / pack_file).exists()
    raw = validate_pack_contract(pack_dir, level="raw")
    assert raw["status"] == "pass", raw

    pack = load_pack(pack_dir)
    assert pack.documents
    assert {record.source_type for record in pack.documents} >= expected_source_types
    assert pack.record_citation_id(pack.documents[0]) == "S1.1"

    prepare_pack(
        pack_dir,
        default_search=False,
        graph=False,
        eval_grade=True,
        max_excerpts=1,
        entity_limit=5,
        search_limit=1,
        graph_entity_limit=1,
    )
    eval_report = validate_pack_contract(pack_dir, level="eval")
    assert eval_report["status"] == "pass", eval_report

    export_dir = pack_dir.parent / f"{pack_dir.name}-exports"
    vector = export_pack(
        pack_dir,
        format="openai-vector-jsonl",
        output=export_dir / "openai.jsonl",
    )
    warehouse = export_pack(
        pack_dir,
        format="warehouse-ndjson",
        output=export_dir / "warehouse.ndjson",
    )
    skill = export_pack(
        pack_dir,
        format="codex-skill",
        output=export_dir / "codex-skill",
        skill_name=f"{pack_dir.name}-typed-pack",
    )
    assert vector.output_path.exists()
    assert warehouse.output_path.exists()
    assert (skill.output_path / "SKILL.md").exists()
    assert vector.record_count == warehouse.record_count == skill.record_count == len(pack.documents)


def _dataset_source(tmp_path: Path) -> Path:
    source = tmp_path / "customers.csv"
    source.write_text(
        "id,name,plan\n1,Ada,pro\n2,Grace,team\n",
        encoding="utf-8",
    )
    return source


def _transcript_source(tmp_path: Path) -> Path:
    source = tmp_path / "meeting.vtt"
    source.write_text(
        """WEBVTT

00:00:01.000 --> 00:00:03.000
Welcome to the launch review.

00:00:04.000 --> 00:00:06.000
The package lane should cite timestamped evidence.
""",
        encoding="utf-8",
    )
    return source


def _paper_source(tmp_path: Path) -> Path:
    source = tmp_path / "paper.md"
    source.write_text(
        """# Efficient Context Dependencies

This paper describes auditable context dependencies for agent systems.

The method keeps citations precise and updates bounded.
""",
        encoding="utf-8",
    )
    return source


def _install_repo_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read(url: str, **_: Any) -> RemoteText:
        if url == "https://api.github.com/repos/acme/widgets":
            return _remote_json(
                url,
                {
                    "html_url": "https://github.com/acme/widgets",
                    "default_branch": "main",
                    "description": "Example widget toolkit.",
                    "license": {"spdx_id": "MIT"},
                },
            )
        if url == "https://api.github.com/repos/acme/widgets/commits/main":
            return _remote_json(url, {"sha": "abc123"})
        if url == "https://api.github.com/repos/acme/widgets/git/trees/abc123?recursive=1":
            return _remote_json(
                url,
                {
                    "tree": [
                        {"path": "README.md", "type": "blob", "size": 64},
                        {"path": "docs/usage.md", "type": "blob", "size": 96},
                        {"path": "src/ignored.py", "type": "blob", "size": 64},
                    ]
                },
            )
        if url == "https://raw.githubusercontent.com/acme/widgets/abc123/README.md":
            return _remote_text(url, "# Widgets\n\nUse widgets for local fixtures.")
        if url == "https://raw.githubusercontent.com/acme/widgets/abc123/docs/usage.md":
            return _remote_text(url, "# Usage\n\nCall `widgets.run()`.")
        if url == "https://api.github.com/repos/acme/widgets/releases?per_page=5":
            return _remote_json(url, [{"tag_name": "v1.0.0", "name": "First release", "body": "Stable."}])
        raise AssertionError(f"Unexpected GitHub URL: {url}")

    monkeypatch.setattr("docpull.context_packs.repo.read_https_text", fake_read)


def _install_package_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read(url: str, **_: Any) -> RemoteText:
        if url == "https://registry.npmjs.org/widgets":
            return _remote_json(
                url,
                {
                    "name": "widgets",
                    "description": "Widget package.",
                    "dist-tags": {"latest": "1.2.3"},
                    "versions": {
                        "1.0.0": {},
                        "1.2.3": {
                            "description": "Widget package.",
                            "license": "MIT",
                            "readme": "# widgets\n\nInstall and run widgets.",
                            "dependencies": {"left-pad": "^1.3.0"},
                            "repository": {"url": "https://github.com/acme/widgets.git"},
                        },
                    },
                },
            )
        raise AssertionError(f"Unexpected package URL: {url}")

    monkeypatch.setattr("docpull.context_packs.package.read_https_text", fake_read)


def _install_standards_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read(url: str, **_: Any) -> RemoteText:
        if url == "https://www.rfc-editor.org/rfc/rfc9999.txt":
            return _remote_text(
                url,
                "RFC 9999\nExample Protocol\n\n1. Introduction\nThis standard references RFC 2119.",
            )
        if url == "https://www.rfc-editor.org/rfc-index.xml":
            return _remote_text(
                url,
                """<rfc-index>
  <rfc-entry>
    <doc-id>RFC9999</doc-id>
    <title>Example Protocol</title>
    <current-status>PROPOSED STANDARD</current-status>
    <date><month>July</month><year>2026</year></date>
    <author><name>A. Example</name></author>
  </rfc-entry>
</rfc-index>""",
                content_type="application/xml",
            )
        raise AssertionError(f"Unexpected standards URL: {url}")

    monkeypatch.setattr("docpull.context_packs.standards.read_https_text", fake_read)


def _install_wiki_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read(url: str, **_: Any) -> RemoteText:
        if url == "https://en.wikipedia.org/w/rest.php/v1/page/Web_scraping/with_html":
            return _remote_json(
                url,
                {
                    "title": "Web scraping",
                    "key": "Web_scraping",
                    "html_url": "https://en.wikipedia.org/wiki/Web_scraping",
                    "latest": {"id": 123, "timestamp": "2026-07-01T00:00:00Z"},
                    "license": {"title": "Creative Commons Attribution-ShareAlike License"},
                    "html": """
                    <section data-mw-section-id="0">
                      <p>Web scraping extracts data from websites.</p>
                    </section>
                    <section data-mw-section-id="1">
                      <h2 id="History">History</h2>
                      <p>Automated web collection has a long history.</p>
                    </section>
                    """,
                },
            )
        raise AssertionError(f"Unexpected wiki URL: {url}")

    monkeypatch.setattr("docpull.context_packs.wiki.read_https_text", fake_read)


def _install_paper_api_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read(url: str, **_: Any) -> RemoteText:
        if url.startswith("https://export.arxiv.org/api/query"):
            return _remote_text(
                url,
                """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/1234.5678</id>
    <title>Typed Context Lanes</title>
    <summary>Typed lanes make context auditable.</summary>
    <published>2026-07-01T00:00:00Z</published>
    <updated>2026-07-02T00:00:00Z</updated>
    <author><name>Ada Example</name></author>
    <category term="cs.AI" />
    <link title="pdf" href="https://arxiv.org/pdf/1234.5678" />
  </entry>
</feed>""",
                content_type="application/atom+xml",
            )
        if url.startswith("https://api.crossref.org/works/10.1000"):
            return _remote_json(
                url,
                {
                    "message": {
                        "URL": "https://doi.org/10.1000/example",
                        "title": ["Crossref Lane"],
                        "abstract": "<p>Crossref metadata abstract.</p>",
                        "author": [{"given": "Grace", "family": "Example"}],
                        "created": {"date-parts": [[2026, 7, 1]]},
                        "DOI": "10.1000/example",
                        "reference": [{"key": "ref1", "DOI": "10.1000/ref"}],
                    }
                },
            )
        if url.startswith("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"):
            return _remote_json(
                url,
                {
                    "result": {
                        "12345": {
                            "title": "PubMed Lane",
                            "authors": [{"name": "Lin Example"}],
                            "pubdate": "2026 Jul",
                            "fulljournalname": "Journal of Context",
                            "articleids": [{"idtype": "doi", "value": "10.1000/pubmed"}],
                        }
                    }
                },
            )
        if url.startswith("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"):
            return _remote_json(
                url,
                {
                    "linksets": [
                        {
                            "linksetdbs": [
                                {
                                    "linkname": "pubmed_pubmed_refs",
                                    "links": ["111", "222"],
                                }
                            ]
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected paper URL: {url}")

    monkeypatch.setattr("docpull.context_packs.paper.ARXIV_API_DELAY_SECONDS", 0.0)
    monkeypatch.setattr("docpull.context_packs.paper.read_https_text", fake_read)


def test_dataset_pack_roundtrips_through_v3_prepare_and_exports(tmp_path: Path) -> None:
    source = _dataset_source(tmp_path)
    pack_dir = tmp_path / "dataset-pack"

    result = build_dataset_pack([source], output_dir=pack_dir)

    assert result["workflow"] == "dataset-pack"
    assert result["summary"]["dataset_count"] == 1
    schema = json.loads((pack_dir / "dataset.schema.json").read_text(encoding="utf-8"))
    assert schema["datasets"][0]["row_count"] == 2
    _assert_pack_roundtrip(pack_dir, pack_file="dataset.pack.json", expected_source_types={"dataset"})


def test_dataset_pack_supports_remote_https_json_with_snapshot_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "https://data.example.test/resource.json?$limit=2&category=bagel&category=cafe"

    def fake_read(url: str, **_kwargs: Any) -> RemoteText:
        assert url == source
        return RemoteText(
            text=json.dumps([{"name": "A", "count": 1}, {"name": "B", "count": 2}]),
            url="https://data.example.test/resource.json?$limit=2&category=bagel&category=cafe",
            content_type="application/json; charset=utf-8",
            status_code=200,
        )

    monkeypatch.setattr("docpull.context_packs.dataset.read_https_text", fake_read)
    output = tmp_path / "remote-dataset"

    result = build_dataset_pack([source], output_dir=output)
    schema = json.loads((output / "dataset.schema.json").read_text(encoding="utf-8"))
    workflow = json.loads((output / "workflow.result.json").read_text(encoding="utf-8"))

    provenance = schema["datasets"][0]["provenance"]
    assert provenance["original_url"] == source
    assert provenance["query_parameters"] == [
        {"name": "$limit", "value": "2"},
        {"name": "category", "value": "bagel"},
        {"name": "category", "value": "cafe"},
    ]
    assert len(provenance["snapshot_hash"]) == 64
    assert result["summary"]["record_count"] == 1
    assert workflow["contract_version"] == "workflow.result.v1"
    assert workflow["workflow"] == "dataset-pack"


def test_transcript_pack_roundtrips_through_v3_prepare_and_exports(tmp_path: Path) -> None:
    source = _transcript_source(tmp_path)
    pack_dir = tmp_path / "transcript-pack"

    result = build_transcript_pack([source], output_dir=pack_dir)

    assert result["workflow"] == "transcript-pack"
    assert result["summary"]["segment_count"] == 2
    assert (pack_dir / "transcript.segments.ndjson").exists()
    _assert_pack_roundtrip(
        pack_dir,
        pack_file="transcript.pack.json",
        expected_source_types={"transcript_segment"},
    )


def test_paper_pack_roundtrips_local_and_api_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_paper_api_mock(monkeypatch)
    local_paper = _paper_source(tmp_path)
    pack_dir = tmp_path / "paper-pack"

    result = build_paper_pack(
        [local_paper, "arxiv:1234.5678", "doi:10.1000/example", "pmid:12345"],
        output_dir=pack_dir,
    )

    assert result["workflow"] == "paper-pack"
    assert result["summary"]["paper_count"] == 4
    assert result["summary"]["reference_count"] == 3
    assert (pack_dir / "paper.metadata.json").exists()
    assert (pack_dir / "paper.references.ndjson").exists()
    _assert_pack_roundtrip(pack_dir, pack_file="paper.pack.json", expected_source_types={"paper"})


def test_paper_pack_include_full_text_parses_arxiv_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_paper_api_mock(monkeypatch)

    def fake_pdf_fetch(url: str) -> bytes:
        assert url == "https://arxiv.org/pdf/1234.5678"
        return b"%PDF-1.7 example"

    def fake_parse_one(path: Path, *, backend: str, source_url: str, title: str | None) -> ParsedDocument:
        assert backend == "auto"
        assert source_url == "https://arxiv.org/pdf/1234.5678"
        assert title == "Typed Context Lanes"
        assert path.read_bytes().startswith(b"%PDF")
        return ParsedDocument(
            path=path,
            source_url=source_url,
            title=title or "Typed Context Lanes",
            content="Parsed arXiv PDF full text.",
            backend="markitdown",
            source_mime_type="application/pdf",
            metadata={},
        )

    monkeypatch.setattr("docpull.context_packs.paper._fetch_arxiv_pdf_bytes", fake_pdf_fetch)
    monkeypatch.setattr("docpull.context_packs.paper.parse_one_document", fake_parse_one)
    pack_dir = tmp_path / "paper-pack"

    result = build_paper_pack(["arxiv:1234.5678"], output_dir=pack_dir, include_full_text=True)

    assert result["validation"]["status"] == "pass"
    pack = load_pack(pack_dir)
    assert "Parsed arXiv PDF full text." in pack.documents[0].content
    assert pack.documents[0].metadata["full_text_status"] == "included_arxiv_pdf"
    assert pack.documents[0].metadata["parse_backend"] == "markitdown"


def test_paper_url_routing_requires_exact_known_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    from docpull.context_packs import paper as paper_module

    monkeypatch.setattr(
        paper_module,
        "_paper_from_arxiv",
        lambda arxiv_id, *, include_full_text: {"route": "arxiv", "id": arxiv_id},
    )
    monkeypatch.setattr(paper_module, "_paper_from_doi", lambda doi: {"route": "doi", "id": doi})
    monkeypatch.setattr(paper_module, "_paper_from_pmid", lambda pmid: {"route": "pmid", "id": pmid})
    monkeypatch.setattr(
        paper_module,
        "_paper_from_metadata_url",
        lambda url: {"route": "metadata", "url": url},
    )

    assert paper_module._paper_from_source("https://arxiv.org/abs/1234.5678", include_full_text=False) == {
        "route": "arxiv",
        "id": "1234.5678",
    }
    assert paper_module._paper_from_source("https://doi.org/10.1000/example", include_full_text=False) == {
        "route": "doi",
        "id": "10.1000/example",
    }
    assert paper_module._paper_from_source(
        "https://pubmed.ncbi.nlm.nih.gov/12345/",
        include_full_text=False,
    ) == {
        "route": "pmid",
        "id": "12345",
    }

    for url in (
        "https://arxiv.org.evil.example/abs/1234.5678",
        "https://doi.org.evil.example/10.1000/example",
        "https://pubmed.ncbi.nlm.nih.gov.evil.example/12345/",
    ):
        assert paper_module._paper_from_source(url, include_full_text=False) == {
            "route": "metadata",
            "url": url,
        }


def test_repo_pack_roundtrips_mocked_github_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_repo_mock(monkeypatch)
    pack_dir = tmp_path / "repo-pack"

    result = build_repo_pack("acme/widgets", output_dir=pack_dir)

    assert result["workflow"] == "repo-pack"
    assert result["summary"]["repo"] == "acme/widgets"
    pack = load_pack(pack_dir)
    urls = {record.url for record in pack.documents}
    assert "github://acme/widgets@abc123/README.md" in urls
    _assert_pack_roundtrip(
        pack_dir,
        pack_file="repo.pack.json",
        expected_source_types={"github_repository", "github_file"},
    )


def test_repo_pack_falls_back_to_archive_when_github_api_is_limited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def limited_api(*_: Any, **__: Any) -> dict[str, Any]:
        raise ValueError("GitHub API rate limit exceeded")

    monkeypatch.setattr("docpull.context_packs.repo._read_json", limited_api)
    monkeypatch.setattr(
        "docpull.context_packs.repo._git_resolve_ref",
        lambda owner, repo, ref: ("main", "main", "abc123"),
    )
    monkeypatch.setattr(
        "docpull.context_packs.repo._read_github_archive_files",
        lambda owner, repo, ref, *, max_items: [
            {"path": "README.md", "size": 80, "text": "# Widgets\n\nArchive fallback description."},
            {"path": "LICENSE", "size": 120, "text": "MIT License\n\nPermission is hereby granted."},
        ],
    )
    monkeypatch.setattr(
        "docpull.context_packs.repo._repo_releases_atom",
        lambda owner, repo: [{"tag_name": "v1.0.0", "name": "v1.0.0", "body": "Release notes."}],
    )
    monkeypatch.setattr(
        "docpull.context_packs.repo._repo_public_html_metadata",
        lambda owner, repo: {"description": "HTML fallback description.", "topics": ["agent-context"]},
    )
    pack_dir = tmp_path / "repo-pack"

    result = build_repo_pack("acme/widgets", output_dir=pack_dir)

    assert result["validation"]["status"] == "pass"
    metadata = json.loads((pack_dir / "repo.metadata.json").read_text(encoding="utf-8"))
    assert metadata["acquisition_method"] == "git_archive_fallback"
    assert metadata["fallback_reason"] == "GitHub API rate limit exceeded"
    assert metadata["description"] == "HTML fallback description."
    assert metadata["description_source"] == "github_html"
    assert metadata["topics"] == ["agent-context"]
    assert metadata["license"]["spdx_id"] == "MIT"
    assert metadata["release_count"] == 1
    pack = load_pack(pack_dir)
    assert {record.source_type for record in pack.documents} == {
        "github_repository",
        "github_file",
        "github_releases",
    }
    assert "Archive fallback description." in pack.documents[1].content


def test_repo_pack_parses_public_github_html_metadata() -> None:
    from docpull.context_packs.repo import _parse_repo_html_metadata

    payload = _parse_repo_html_metadata(
        """
        <html>
          <head>
            <meta property="og:description" content="GitHub - acme/widgets: Public widget toolkit">
          </head>
          <body>
            <a class="topic-tag">python</a>
            <a class="topic-tag">python</a>
            <a class="topic-tag">agents</a>
            <a rel="nofollow me" href="https://widgets.example.com">homepage</a>
          </body>
        </html>
        """,
        owner="acme",
        repo="widgets",
    )

    assert payload == {
        "description": "Public widget toolkit",
        "topics": ["python", "agents"],
        "homepage": "https://widgets.example.com",
    }


def test_repo_pack_git_http_ref_resolution_avoids_local_git(monkeypatch: pytest.MonkeyPatch) -> None:
    from docpull.context_packs import repo as repo_module

    monkeypatch.setattr(
        repo_module,
        "_read_git_upload_pack_refs",
        lambda url: {
            "default_branch": "main",
            "head_sha": "a" * 40,
            "refs": {"refs/heads/main": "a" * 40, "refs/tags/v1": "b" * 40},
        },
    )
    monkeypatch.setattr(repo_module, "_run_git", lambda args: (_ for _ in ()).throw(AssertionError))

    assert repo_module._git_resolve_ref("acme", "widgets", None) == ("main", "main", "a" * 40)
    assert repo_module._git_resolve_ref("acme", "widgets", "v1") == ("v1", "v1", "b" * 40)


def test_package_pack_roundtrips_mocked_npm_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_package_mock(monkeypatch)
    pack_dir = tmp_path / "package-pack"

    result = build_package_pack("npm:widgets", output_dir=pack_dir)

    assert result["workflow"] == "package-pack"
    assert result["summary"]["ecosystem"] == "npm"
    assert result["summary"]["latest_version"] == "1.2.3"
    assert (pack_dir / "package.metadata.json").exists()
    records = [
        json.loads(line)
        for line in (pack_dir / "documents.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records[0]["rights"]["allowed_use"]["eval_generation"] == "allowed_with_conditions"
    assert records[0]["rights"]["obligations"] == ["comply with package license terms"]
    _assert_pack_roundtrip(
        pack_dir,
        pack_file="package.pack.json",
        expected_source_types={"package_metadata", "package_document", "package_version"},
    )


def test_package_github_repo_source_requires_exact_github_host() -> None:
    from docpull.context_packs.package import _github_repo_source

    assert _github_repo_source("git://github.com/acme/widgets.git") == "https://github.com/acme/widgets"
    assert _github_repo_source("git+https://github.com/acme/widgets.git") == "https://github.com/acme/widgets"
    assert _github_repo_source("https://github.com.evil.example/acme/widgets") is None
    assert _github_repo_source("https://github.com@evil.example/acme/widgets") is None


def test_standards_pack_roundtrips_mocked_rfc_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_standards_mock(monkeypatch)
    pack_dir = tmp_path / "standards-pack"

    result = build_standards_pack(["rfc:9999"], output_dir=pack_dir)

    assert result["workflow"] == "standards-pack"
    assert result["summary"]["standard_count"] == 1
    metadata = json.loads((pack_dir / "standards.metadata.json").read_text(encoding="utf-8"))
    assert metadata["standards"][0]["sections"][0]["label"] == "1"
    assert "content" not in metadata["standards"][0]["sections"][0]
    pack = load_pack(pack_dir)
    assert "standard_section" in {record.source_type for record in pack.documents}
    _assert_pack_roundtrip(
        pack_dir,
        pack_file="standards.pack.json",
        expected_source_types={"standard", "standard_section"},
    )


def test_wiki_pack_roundtrips_mocked_mediawiki_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_wiki_mock(monkeypatch)
    pack_dir = tmp_path / "wiki-pack"

    result = build_wiki_pack(["wiki:Web_scraping"], output_dir=pack_dir)

    assert result["workflow"] == "wiki-pack"
    assert result["summary"]["page_count"] == 1
    assert (pack_dir / "wiki.metadata.json").exists()
    assert (pack_dir / "wiki.sections.ndjson").exists()
    pack = load_pack(pack_dir)
    assert "Web scraping extracts data" in "\n".join(record.content for record in pack.documents)
    assert {record.source_type for record in pack.documents} >= {"wiki_page", "wiki_section"}
    records = [
        json.loads(line)
        for line in (pack_dir / "documents.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records[0]["rights"]["allowed_use"]["redistribution"] == "allowed_with_conditions"
    assert records[0]["rights"]["obligations"] == ["provide attribution and preserve license terms"]
    _assert_pack_roundtrip(
        pack_dir,
        pack_file="wiki.pack.json",
        expected_source_types={"wiki_page", "wiki_section"},
    )


def test_official_source_contracts_are_host_allowlisted() -> None:
    with pytest.raises(ValueError, match="does not allow"):
        read_https_text(
            "https://example.com/query",
            accept="application/json",
            source_contract="crossref_api",
        )
    with pytest.raises(ValueError, match="MediaWiki REST endpoint"):
        read_https_text(
            "https://en.wikipedia.org/wiki/Web_scraping",
            accept="application/json",
            source_contract="mediawiki_rest",
        )


def test_typed_http_cache_reuses_matching_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FakeClient:
        user_agent = "docpull-test"

        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> Any:
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                content_type="application/json",
                url=url,
            )

    monkeypatch.setattr("docpull.context_packs.typed.AsyncHttpClient", FakeClient)
    with typed_http_cache(tmp_path / "cache"):
        first = read_https_text(
            "https://api.crossref.org/works/10.1000/example",
            accept="application/json",
            source_contract="crossref_api",
        )
        second = read_https_text(
            "https://api.crossref.org/works/10.1000/example",
            accept="application/json",
            source_contract="crossref_api",
        )

    assert calls == 1
    assert first.text == second.text == '{"ok": true}'


@pytest.mark.asyncio
async def test_async_typed_pack_wrapper_works_inside_event_loop(tmp_path: Path) -> None:
    from docpull.context_packs.dataset import async_build_dataset_pack

    source = _dataset_source(tmp_path)
    result = await async_build_dataset_pack([source], output_dir=tmp_path / "dataset-async")

    assert result["workflow"] == "dataset-pack"
    assert result["validation"]["status"] == "pass"


@pytest.mark.parametrize(
    ("command_factory", "mock_installer", "workflow"),
    [
        (lambda tmp: ["dataset-pack", str(_dataset_source(tmp))], None, "dataset-pack"),
        (lambda tmp: ["transcript-pack", str(_transcript_source(tmp))], None, "transcript-pack"),
        (lambda tmp: ["paper-pack", str(_paper_source(tmp))], None, "paper-pack"),
        (lambda _tmp: ["repo-pack", "acme/widgets"], _install_repo_mock, "repo-pack"),
        (lambda _tmp: ["package-pack", "npm:widgets"], _install_package_mock, "package-pack"),
        (lambda _tmp: ["standards-pack", "rfc:9999"], _install_standards_mock, "standards-pack"),
        (lambda _tmp: ["wiki-pack", "wiki:Web_scraping"], _install_wiki_mock, "wiki-pack"),
    ],
)
def test_typed_pack_cli_json_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command_factory: Callable[[Path], list[str]],
    mock_installer: Callable[[pytest.MonkeyPatch], None] | None,
    workflow: str,
) -> None:
    if mock_installer:
        mock_installer(monkeypatch)
    pack_dir = tmp_path / workflow
    command = command_factory(tmp_path) + ["-o", str(pack_dir), "--json"]

    assert main(command) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == workflow
    assert payload["validation"]["status"] == "pass"
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"


LIVE_TYPED_PACKS = pytest.mark.skipif(
    os.environ.get("DOCPULL_LIVE_TYPED_PACKS") != "1",
    reason="set DOCPULL_LIVE_TYPED_PACKS=1 to run live typed-pack smoke tests",
)


@LIVE_TYPED_PACKS
@pytest.mark.parametrize(
    ("builder", "source", "extra_kwargs"),
    [
        (
            build_openapi_pack,
            "https://raw.githubusercontent.com/swagger-api/swagger-petstore/master/src/main/resources/openapi.yaml",
            {},
        ),
        (build_feed_pack, "https://blog.python.org/feeds/posts/default", {"max_items": 3}),
        (build_paper_pack, ["arxiv:1706.03762"], {"max_items": 3}),
        (build_paper_pack, ["doi:10.1038/nphys1170"], {"max_items": 3}),
        (build_paper_pack, ["pmid:31452104"], {"max_items": 3}),
        (build_repo_pack, "psf/requests", {"max_items": 3}),
        (build_package_pack, "npm:is-even", {"max_items": 3}),
        (build_package_pack, "pypi:requests", {"max_items": 3}),
        (build_standards_pack, ["rfc:9110"], {"max_items": 3}),
        (build_wiki_pack, ["wiki:Web_scraping"], {"max_items": 3}),
    ],
)
def test_live_typed_pack_smokes(
    tmp_path: Path,
    builder: Callable[..., dict[str, Any]],
    source: Any,
    extra_kwargs: dict[str, Any],
) -> None:
    kwargs = {"output_dir": tmp_path / "pack", **extra_kwargs}
    result = builder(source, **kwargs)

    assert result["validation"]["status"] == "pass"
    assert validate_pack_contract(tmp_path / "pack", level="raw")["status"] == "pass"

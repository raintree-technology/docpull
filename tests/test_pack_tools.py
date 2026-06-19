"""Pack inspection tool tests."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.cli import main
from docpull.pack_tools import (
    build_citation_map,
    build_research_brief,
    diff_packs,
    extract_pack_entities,
    prepare_pack,
    score_pack,
    score_pack_sources,
    search_pack,
)


def _write_pack(pack_dir: Path, records: list[dict[str, object]], *, include_domains: list[str]) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = pack_dir / "sources"
    sources_dir.mkdir(exist_ok=True)
    sources = []
    for index, record in enumerate(records, start=1):
        source_path = sources_dir / f"{index:02d}.md"
        source_path.write_text(str(record["content"]), encoding="utf-8")
        sources.append(
            {
                "index": index,
                "url": record["url"],
                "title": record["title"],
                "path": f"sources/{index:02d}.md",
            }
        )
    (pack_dir / "documents.ndjson").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    (pack_dir / "corpus.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "document_count": len({record["document_id"] for record in records}),
                "record_count": len(records),
                "records": [
                    {
                        "document_id": record["document_id"],
                        "url": record["url"],
                        "content_hash": record["content_hash"],
                    }
                    for record in records
                ],
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "parallel.pack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "parallel",
                "workflow": "context-pack",
                "request_options": {"source_policy": {"include_domains": include_domains}},
                "extract_error_count": 0,
                "record_count": len(records),
                "sources": sources,
                "artifacts": {
                    "documents_ndjson": "documents.ndjson",
                    "corpus_manifest": "corpus.manifest.json",
                    "sources": "sources.md",
                },
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "sources.md").write_text("# Sources\n", encoding="utf-8")


def _record(url: str, content_hash: str, content: str = "content") -> dict[str, object]:
    return {
        "document_id": f"doc_{content_hash}",
        "url": url,
        "title": url,
        "content": content,
        "content_hash": content_hash,
        "source_type": "parallel_extract",
    }


def test_pack_score_flags_off_domain_sources(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record("https://parallel.ai/products/search", "aaa"),
            _record("https://example.com/wrong", "bbb"),
        ],
        include_domains=["parallel.ai"],
    )

    result = score_pack(pack)

    assert result["score"] < 100
    assert result["issues"][0]["code"] == "off_domain_sources"
    assert result["expected_domains"] == ["parallel.ai"]


def test_pack_score_cli_writes_score_file(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack, [_record("https://parallel.ai/products/search", "aaa")], include_domains=["parallel.ai"]
    )

    assert main(["pack", "score", str(pack), "--min-score", "80"]) == 0

    score_path = pack / "pack.score.json"
    payload = json.loads(score_path.read_text(encoding="utf-8"))
    assert payload["score"] >= 80


def test_pack_score_understands_search_pack_metadata(tmp_path: Path) -> None:
    pack = tmp_path / "search-pack"
    records = [_record("https://docs.parallel.ai/api-reference/search/search", "aaa")]
    _write_pack(pack, records, include_domains=["docs.parallel.ai"])
    (pack / "parallel.pack.json").unlink()
    (pack / "search.pack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "parallel",
                "workflow": "search-pack",
                "metadata": {
                    "request_options": {
                        "source_policy": {"include_domains": ["docs.parallel.ai"]},
                    },
                },
                "record_count": len(records),
                "sources": [
                    {
                        "index": 1,
                        "url": records[0]["url"],
                        "title": records[0]["title"],
                        "path": "sources/01.md",
                    }
                ],
                "artifacts": {
                    "documents_ndjson": "documents.ndjson",
                    "corpus_manifest": "corpus.manifest.json",
                    "sources": "sources.md",
                },
            }
        ),
        encoding="utf-8",
    )

    result = score_pack(pack)

    assert result["score"] == 100
    assert result["expected_domains"] == ["docs.parallel.ai"]
    assert result["warnings"] == []


def test_pack_sources_ranks_expected_docs_sources(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record("https://parallel.ai/blog/search", "aaa"),
            _record("https://docs.parallel.ai/api-reference/search/search", "bbb"),
        ],
        include_domains=["docs.parallel.ai"],
    )

    result = score_pack_sources(pack)

    assert result["sources"][0]["url"] == "https://docs.parallel.ai/api-reference/search/search"
    assert result["sources"][0]["grade"] == "primary"
    assert result["sources"][1]["grade"] == "review"


def test_pack_sources_cli_writes_source_scores(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [_record("https://docs.parallel.ai/api-reference/search/search", "aaa")],
        include_domains=["docs.parallel.ai"],
    )

    assert main(["pack", "sources", str(pack)]) == 0

    payload = json.loads((pack / "source.scores.json").read_text(encoding="utf-8"))
    assert payload["source_count"] == 1
    assert payload["sources"][0]["score"] >= 85


def test_pack_citations_cli_writes_stable_source_map(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record("https://docs.parallel.ai/api-reference/search/search", "aaa"),
            _record("https://docs.parallel.ai/api-reference/search/search", "bbb"),
            _record("https://parallel.ai/blog/search", "ccc"),
        ],
        include_domains=["docs.parallel.ai"],
    )

    library_payload = build_citation_map(pack)
    assert library_payload["source_count"] == 2

    assert main(["pack", "citations", str(pack), "--markdown", str(tmp_path / "citations.md")]) == 0

    payload = json.loads((pack / "citations.json").read_text(encoding="utf-8"))
    assert payload["source_count"] == 2
    assert payload["sources"][0]["citation_id"] == "S1"
    assert payload["sources"][0]["record_count"] == 2
    assert payload["sources"][0]["path"] == "sources/01.md"
    assert "[S1]" in (tmp_path / "citations.md").read_text(encoding="utf-8")


def test_pack_entities_extracts_cited_local_records(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record(
                "https://docs.parallel.ai/api-reference/search/search",
                "aaa",
                (
                    "Contact support@example.com. Parallel Web Systems raised $100M on "
                    "2026-04-29. Use Search API version 1.2.3 for JSON output."
                ),
            )
        ],
        include_domains=["docs.parallel.ai"],
    )

    result = extract_pack_entities(pack, limit=20)
    by_type = {(entity["type"], entity["normalized"]): entity for entity in result["entities"]}

    assert ("email", "support@example.com") in by_type
    assert ("money", "$100m") in by_type
    assert ("date", "2026-04-29") in by_type
    assert ("version", "1.2.3") in by_type
    assert by_type[("email", "support@example.com")]["citations"][0]["citation_id"] == "S1"


def test_pack_entities_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record(
                "https://docs.parallel.ai/api-reference/search/search",
                "aaa",
                "Search API returns JSON records. Contact support@example.com for access.",
            )
        ],
        include_domains=["docs.parallel.ai"],
    )

    assert main(["pack", "entities", str(pack), "--markdown", str(tmp_path / "entities.md")]) == 0

    payload = json.loads((pack / "entities.json").read_text(encoding="utf-8"))
    values = {entity["normalized"] for entity in payload["entities"]}
    assert "support@example.com" in values
    assert "support@example.com" in (tmp_path / "entities.md").read_text(encoding="utf-8")


def test_pack_search_returns_ranked_cited_excerpts(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record(
                "https://docs.parallel.ai/api-reference/search/search",
                "aaa",
                (
                    "Parallel Search API supports live web search for agents. "
                    "Search results include cited excerpts, JSON records, and source controls."
                ),
            ),
            _record(
                "https://docs.parallel.ai/api-reference/extract/extract",
                "bbb",
                "Extract turns known URLs into markdown content for context packs.",
            ),
        ],
        include_domains=["docs.parallel.ai"],
    )

    result = search_pack(pack, "live search agents", limit=5)

    assert result["result_count"] == 1
    assert result["results"][0]["citation_id"].startswith("S")
    assert result["results"][0]["url"] == "https://docs.parallel.ai/api-reference/search/search"
    assert result["results"][0]["matched_terms"] == ["agents", "live", "search"]
    assert "live web search" in result["results"][0]["excerpt"]
    assert result["citations"][0]["citation_id"] == result["results"][0]["citation_id"]


def test_pack_search_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record(
                "https://docs.parallel.ai/api-reference/search/search",
                "aaa",
                "Search API results include citations and JSON output for agent workflows.",
            )
        ],
        include_domains=["docs.parallel.ai"],
    )

    assert (
        main(
            [
                "pack",
                "search",
                str(pack),
                "citations JSON",
                "--markdown",
                str(tmp_path / "search.md"),
            ]
        )
        == 0
    )

    payload = json.loads((pack / "pack.search.json").read_text(encoding="utf-8"))
    assert payload["query"] == "citations JSON"
    assert payload["result_count"] == 1
    assert "[S" in (tmp_path / "search.md").read_text(encoding="utf-8")


def test_pack_brief_cli_writes_cited_brief_and_sidecars(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record(
                "https://docs.parallel.ai/api-reference/search/search",
                "aaa",
                (
                    "Parallel Search API pricing and source controls are documented for agent "
                    "builders. The feature list explains search results, citations, and JSON "
                    "responses for live web workflows."
                ),
            ),
            _record(
                "https://docs.parallel.ai/api-reference/extract/extract",
                "bbb",
                (
                    "Parallel Extract API turns selected URLs into markdown excerpts. This "
                    "source is useful when a context pack needs structured cited content."
                ),
            ),
        ],
        include_domains=["docs.parallel.ai"],
    )

    assert main(["pack", "brief", str(pack), "--objective", "Parallel API pricing"]) == 0

    payload = json.loads((pack / "research.brief.json").read_text(encoding="utf-8"))
    markdown = (pack / "RESEARCH_BRIEF.md").read_text(encoding="utf-8")
    assert payload["objective"] == "Parallel API pricing"
    assert payload["key_excerpts"][0]["citation_id"].startswith("S")
    assert any("pricing" in excerpt["excerpt"].lower() for excerpt in payload["key_excerpts"])
    assert payload["artifacts"]["citations"] == "citations.json"
    assert (pack / "citations.json").exists()
    assert (pack / "entities.json").exists()
    assert "[S" in markdown


def test_pack_brief_library_uses_pack_objective_when_omitted(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [_record("https://docs.parallel.ai/api-reference/search/search", "aaa")],
        include_domains=["docs.parallel.ai"],
    )
    pack_path = pack / "parallel.pack.json"
    metadata = json.loads(pack_path.read_text(encoding="utf-8"))
    metadata["objective"] = "Build a Parallel docs pack"
    pack_path.write_text(json.dumps(metadata), encoding="utf-8")

    result = build_research_brief(pack)

    assert result["objective"] == "Build a Parallel docs pack"


def test_pack_prepare_writes_standard_intelligence_bundle(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [
            _record(
                "https://docs.parallel.ai/api-reference/search/search",
                "aaa",
                (
                    "Parallel Search API supports live search, cited JSON results, "
                    "and source controls for agent context packs. Contact "
                    "support@example.com for API access."
                ),
            ),
            _record(
                "https://docs.parallel.ai/api-reference/extract/extract",
                "bbb",
                "Parallel Extract API turns selected URLs into markdown context.",
            ),
        ],
        include_domains=["docs.parallel.ai"],
    )

    result = prepare_pack(
        pack,
        objective="Review Parallel API search",
        search_queries=["live search JSON", "extract markdown"],
    )

    assert result["summary"]["score"] == 100
    assert result["summary"]["search_query_count"] == 2
    assert result["summary"]["search_result_count"] >= 2
    assert result["summary"]["artifact_count"] == len(result["artifacts"])
    assert result["artifacts"]["prepare"] == "pack.prepare.json"
    assert "searches" in result["artifacts"]
    assert (pack / "pack.score.json").exists()
    assert (pack / "source.scores.json").exists()
    assert (pack / "citations.json").exists()
    assert (pack / "CITATIONS.md").exists()
    assert (pack / "entities.json").exists()
    assert (pack / "ENTITIES.md").exists()
    assert (pack / "pack.search.json").exists()
    assert (pack / "pack.searches.json").exists()
    assert (pack / "SEARCH.md").exists()
    assert (pack / "research.brief.json").exists()
    assert (pack / "RESEARCH_BRIEF.md").exists()
    assert (pack / "pack.prepare.json").exists()


def test_pack_prepare_cli_can_skip_search_and_markdown(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [_record("https://docs.parallel.ai/api-reference/search/search", "aaa")],
        include_domains=["docs.parallel.ai"],
    )

    assert main(["pack", "prepare", str(pack), "--no-search", "--no-markdown"]) == 0

    payload = json.loads((pack / "pack.prepare.json").read_text(encoding="utf-8"))
    assert payload["summary"]["search_query_count"] == 0
    assert "search" not in payload["artifacts"]
    assert "brief_markdown" not in payload["artifacts"]
    assert (pack / "pack.score.json").exists()
    assert (pack / "research.brief.json").exists()
    assert not (pack / "SEARCH.md").exists()


def test_pack_score_flags_manifest_and_pack_record_count_mismatch(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [_record("https://parallel.ai/products/search", "aaa")],
        include_domains=["parallel.ai"],
    )

    manifest_path = pack / "corpus.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["record_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    pack_path = pack / "parallel.pack.json"
    parallel_pack = json.loads(pack_path.read_text(encoding="utf-8"))
    parallel_pack["record_count"] = 3
    pack_path.write_text(json.dumps(parallel_pack), encoding="utf-8")

    result = score_pack(pack)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "manifest_record_count_mismatch" in issue_codes
    assert "pack_record_count_mismatch" in issue_codes
    assert result["score"] < 100


def test_pack_score_flags_missing_declared_artifacts_and_sources(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(
        pack,
        [_record("https://parallel.ai/products/search", "aaa")],
        include_domains=["parallel.ai"],
    )

    (pack / "sources" / "01.md").unlink()
    (pack / "sources.md").unlink()

    result = score_pack(pack)

    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["missing_declared_artifacts"]["paths"] == ["sources.md"]
    assert issues["missing_declared_sources"]["paths"] == ["sources/01.md"]


def test_pack_diff_reports_added_removed_and_changed_urls(tmp_path: Path) -> None:
    old_pack = tmp_path / "old"
    new_pack = tmp_path / "new"
    _write_pack(
        old_pack,
        [
            _record("https://parallel.ai/a", "aaa"),
            _record("https://parallel.ai/removed", "bbb"),
            _record("https://parallel.ai/changed", "old"),
        ],
        include_domains=["parallel.ai"],
    )
    _write_pack(
        new_pack,
        [
            _record("https://parallel.ai/a", "aaa"),
            _record("https://parallel.ai/added", "ccc"),
            _record("https://parallel.ai/changed", "new"),
        ],
        include_domains=["parallel.ai"],
    )

    result = diff_packs(old_pack, new_pack)

    assert result["added_urls"] == ["https://parallel.ai/added"]
    assert result["removed_urls"] == ["https://parallel.ai/removed"]
    assert result["changed_urls"] == ["https://parallel.ai/changed"]


def test_pack_diff_cli_writes_outputs(tmp_path: Path) -> None:
    old_pack = tmp_path / "old"
    new_pack = tmp_path / "new"
    _write_pack(old_pack, [_record("https://parallel.ai/a", "aaa")], include_domains=["parallel.ai"])
    _write_pack(new_pack, [_record("https://parallel.ai/b", "bbb")], include_domains=["parallel.ai"])

    assert (
        main(
            [
                "pack",
                "diff",
                str(old_pack),
                str(new_pack),
                "--markdown",
                str(tmp_path / "diff.md"),
            ]
        )
        == 0
    )

    assert (new_pack / "pack.diff.json").exists()
    assert "Added" in (tmp_path / "diff.md").read_text(encoding="utf-8")

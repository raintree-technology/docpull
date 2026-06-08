"""Pack inspection tool tests."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.cli import main
from docpull.pack_tools import diff_packs, score_pack, score_pack_sources


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

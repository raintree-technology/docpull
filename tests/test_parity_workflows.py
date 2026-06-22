"""Provider-neutral local parity workflow tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull.cli import main
from docpull.parity import (
    crawl_pack,
    entities_pack,
    extract_pack,
    map_sources,
    research_pack,
    validate_structured_output,
)
from tests.pack_fixtures import write_context_pack


def test_map_sources_writes_discovery_and_lifecycle_artifacts(tmp_path: Path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text("https://docs.example.com/a\nhttps://docs.example.com/b\n", encoding="utf-8")
    output = tmp_path / "map"

    payload = map_sources(urls, source_type="urls", output_dir=output, query="docs")

    assert payload["workflow"] == "map"
    assert payload["summary"]["candidate_count"] == 2
    assert (output / "candidate_sources.ndjson").exists()
    assert (output / "map.result.json").exists()
    assert (output / "events.ndjson").exists()
    assert json.loads((output / "status.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert "sample_only" in (output / "webhook.sample.json").read_text(encoding="utf-8")


def test_extract_pack_fetches_known_urls_with_local_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFetcher:
        def __init__(self, _config: object) -> None:
            pass

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def fetch_one(self, url: str, *, save: bool) -> SimpleNamespace:
            assert save is False
            return SimpleNamespace(
                error=None,
                should_skip=False,
                markdown=f"Extracted content from {url}",
                title="Extracted",
                metadata={},
                extraction_info={},
                source_type="fake",
            )

    monkeypatch.setattr("docpull.parity.Fetcher", FakeFetcher)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://docs.example.com/a\n", encoding="utf-8")
    output = tmp_path / "extract"

    payload = extract_pack(urls, output_dir=output)

    assert payload["summary"]["record_count"] == 1
    assert (output / "documents.ndjson").exists()
    assert (output / "local.pack.json").exists()
    assert "Extracted content" in (output / "sources" / "001.md").read_text(encoding="utf-8")


def test_crawl_pack_dry_run_selects_discovery_candidates(tmp_path: Path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text(
        "\n".join(
            [
                "https://docs.example.com/a",
                "https://docs.example.com/b",
                "https://docs.example.com/c",
            ]
        ),
        encoding="utf-8",
    )
    discovery = tmp_path / "discovery"
    map_sources(urls, source_type="urls", output_dir=discovery)

    payload = crawl_pack(discovery, output_dir=tmp_path / "crawl", selectors=["top:2"], dry_run=True)

    assert payload["status"] == "dry_run"
    assert payload["summary"]["selected_count"] == 2
    assert (tmp_path / "crawl" / "selected_sources.ndjson").exists()
    assert (tmp_path / "crawl" / "crawl.result.json").exists()


def test_research_pack_validates_local_structured_output(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["summary", "citations"],
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string"},
                    "citations": {"type": "array"},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = research_pack(
        pack,
        objective="What does Parallel Search return?",
        output_dir=tmp_path / "research",
        schema_path=schema,
    )

    assert payload["workflow"] == "research-pack"
    assert payload["summary"]["structured_output_valid"] is True
    assert payload["structured_output"]["validation"]["valid"] is True
    assert (tmp_path / "research" / "research.result.json").exists()
    assert (tmp_path / "research" / "poll.report.json").exists()


def test_entities_pack_writes_entity_result_and_citation_basis(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)

    payload = entities_pack(pack, output_dir=tmp_path / "entities")

    assert payload["workflow"] == "entities-pack"
    assert payload["summary"]["source_count"] == 1
    assert (tmp_path / "entities" / "entities.result.json").exists()
    assert (tmp_path / "entities" / "citations.json").exists()


def test_structured_output_validator_reports_missing_required_fields() -> None:
    payload = validate_structured_output(
        {"summary": "Only one field"},
        {
            "type": "object",
            "required": ["summary", "citations"],
            "properties": {"summary": {"type": "string"}, "citations": {"type": "array"}},
        },
    )

    assert payload["valid"] is False
    assert "$.citations: required property is missing" in payload["errors"]


@pytest.mark.parametrize(
    "argv",
    [
        ["extract-pack", "--help"],
        ["map", "--help"],
        ["map", "urls", "--help"],
        ["crawl-pack", "--help"],
        ["research-pack", "--help"],
        ["entities-pack", "--help"],
    ],
)
def test_parity_command_help_paths(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code == 0

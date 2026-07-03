"""DocPull output contract v3 tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docpull.cli import main
from docpull.conversion.chunking import Chunk
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack
from docpull.pack_tools import prepare_pack
from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.save_ndjson import NdjsonSaveStep
from docpull.pipeline.steps.save_sqlite import SqliteSaveStep


async def _write_v3_ndjson_pack(pack: Path, *, content: str | None = None) -> None:
    step = NdjsonSaveStep(base_output_dir=pack, filename="documents.ndjson")
    ctx = PageContext(
        url="https://news.example.com/events",
        output_path=pack / "events.md",
        markdown=content
        or (
            "# Events\n\n"
            "Live event coverage for local output contract tests with enough words "
            "to support pack brief extraction and validation."
        ),
        title="Events",
        content_type="text/html; charset=utf-8",
        status_code=200,
        bytes_downloaded=512,
    )
    await step.execute(ctx)
    step.finalize()


@pytest.mark.asyncio
async def test_pack_validate_raw_passes_for_v3_ndjson_output(tmp_path: Path) -> None:
    await _write_v3_ndjson_pack(tmp_path)

    payload = validate_pack_contract(tmp_path, level="raw")

    assert payload["status"] == "pass"
    manifest = json.loads((tmp_path / "corpus.manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    assert (tmp_path / "sources.md").exists()
    assert (tmp_path / "acquisition.routes.json").exists()


@pytest.mark.asyncio
async def test_pack_validate_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    await _write_v3_ndjson_pack(tmp_path)

    assert main(["pack", "validate", str(tmp_path), "--format", "json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "pass"
    assert payload["level"] == "raw"


@pytest.mark.asyncio
async def test_pack_prepare_produces_agent_and_eval_contract_sidecars(tmp_path: Path) -> None:
    await _write_v3_ndjson_pack(tmp_path)

    prepare_pack(tmp_path, default_search=False, graph=False, eval_grade=True)

    assert validate_pack_contract(tmp_path, level="agent")["status"] == "pass"
    assert validate_pack_contract(tmp_path, level="eval")["status"] == "pass"
    citation_index = json.loads((tmp_path / "citation.index.json").read_text(encoding="utf-8"))
    assert citation_index["schema_version"] == 3
    assert citation_index["entries"][0]["record_citation_id"] == "S1.1"


@pytest.mark.asyncio
async def test_pack_prepare_extracts_listing_items_from_link_dense_pages(tmp_path: Path) -> None:
    content = """# Recent Events

- [Solar flare event update](https://news.example.com/events/solar-flare)
- [Station repair spacewalk](https://news.example.com/events/spacewalk)
- [Weather delays launch](https://news.example.com/events/launch-delay)
"""
    await _write_v3_ndjson_pack(tmp_path, content=content)

    result = prepare_pack(tmp_path, default_search=False, graph=False)

    assert result["artifacts"]["listing_items"] == "listing.items.ndjson"
    items = [
        json.loads(line)
        for line in (tmp_path / "listing.items.ndjson").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["item_citation_id"] for item in items] == ["I1", "I2", "I3"]
    assert items[0]["parent_record_key"].startswith("doc_")


@pytest.mark.asyncio
async def test_sqlite_chunk_output_loads_with_precise_record_citations(tmp_path: Path) -> None:
    step = SqliteSaveStep(tmp_path, emit_chunks=True)
    ctx = PageContext(
        url="https://docs.example.com/chunked",
        output_path=tmp_path / "chunked.md",
        markdown="full body",
        title="Chunked",
        chunks=[
            Chunk(index=0, text="alpha orbital setup", token_count=3, heading="Alpha"),
            Chunk(index=1, text="beta orbital setup", token_count=3, heading="Beta"),
        ],
    )

    await step.execute(ctx)
    step.close()

    pack = load_pack(tmp_path)
    assert len(pack.documents) == 2
    assert pack.record_citation_id(pack.documents[0]) == "S1.1"
    assert pack.record_citation_id(pack.documents[1]) == "S1.2"

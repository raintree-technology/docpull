"""Tests for the LangChain and LlamaIndex pack loaders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from docpull.integrations._records import iter_pack_records
from docpull.integrations.langchain import DocpullPackLoader
from docpull.integrations.llamaindex import DocpullPackReader
from docpull.pack_reader import PackReadError
from tests.pack_fixtures import write_context_pack

_RECORDS = [
    {
        "document_id": "doc_search",
        "url": "https://docs.parallel.ai/api-reference/search/search",
        "title": "Parallel Search API",
        "content": "Parallel Search API returns cited JSON results.",
        "content_hash": "hash_search",
        "source_type": "parallel_extract",
        "token_count": 8,
    },
    {
        "document_id": "doc_chunked",
        "url": "https://docs.parallel.ai/guides/chunking",
        "title": "Chunking Guide",
        "content": "Chunk one of the chunking guide.",
        "content_hash": "hash_chunk_1",
        "source_type": "parallel_extract",
        "chunk_id": "chunk_a",
        "chunk_index": 0,
        "token_count": 6,
    },
]


def test_iter_pack_records_reads_ndjson_in_order(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack, records=_RECORDS)

    records = list(iter_pack_records(pack))

    assert [record["metadata"]["document_id"] for record in records] == ["doc_search", "doc_chunked"]
    first = records[0]
    assert first["content"] == "Parallel Search API returns cited JSON results."
    assert first["metadata"] == {
        "url": "https://docs.parallel.ai/api-reference/search/search",
        "title": "Parallel Search API",
        "document_id": "doc_search",
        "chunk_id": None,
        "content_hash": "hash_search",
        "token_count": 8,
        "source": "https://docs.parallel.ai/api-reference/search/search",
    }
    chunked = records[1]
    assert chunked["metadata"]["chunk_id"] == "chunk_a"
    assert chunked["metadata"]["token_count"] == 6


def test_iter_pack_records_reads_documents_jsonl(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "documents.jsonl").write_text(
        "\n".join(json.dumps(record) for record in _RECORDS) + "\n",
        encoding="utf-8",
    )

    records = list(iter_pack_records(pack))

    assert [record["metadata"]["document_id"] for record in records] == ["doc_search", "doc_chunked"]
    assert records[0]["metadata"]["source"] == _RECORDS[0]["url"]
    assert records[1]["metadata"]["chunk_id"] == "chunk_a"


def test_iter_pack_records_jsonl_fills_missing_hash_and_id(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "documents.jsonl").write_text(
        json.dumps({"url": "https://example.com/a", "content": "Alpha content."}) + "\n",
        encoding="utf-8",
    )

    (record,) = list(iter_pack_records(pack))

    assert record["metadata"]["document_id"].startswith("doc_")
    assert len(record["metadata"]["content_hash"]) == 64
    assert record["metadata"]["title"] is None


def test_iter_pack_records_jsonl_rejects_records_without_url(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "documents.jsonl").write_text(json.dumps({"content": "no url"}) + "\n", encoding="utf-8")

    with pytest.raises(PackReadError, match="missing url"):
        list(iter_pack_records(pack))


def test_iter_pack_records_falls_back_to_manifest_output_paths(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    sources = pack / "sources"
    sources.mkdir(parents=True)
    (sources / "01.md").write_text("First markdown body.", encoding="utf-8")
    (sources / "02.md").write_text("Second markdown body.", encoding="utf-8")
    (pack / "corpus.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "document_count": 2,
                "record_count": 2,
                "records": [
                    {
                        "document_id": "doc_first",
                        "url": "https://example.com/first",
                        "title": "First",
                        "content_hash": "hash_first",
                        "token_count": 3,
                        "output_path": "sources/01.md",
                    },
                    {
                        "document_id": "doc_second",
                        "url": "https://example.com/second",
                        "title": "Second",
                        "content_hash": "hash_second",
                        "chunk_id": "chunk_second",
                        "chunk_index": 0,
                        "token_count": 3,
                        "output_path": "sources/02.md",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    records = list(iter_pack_records(pack))

    assert [record["content"] for record in records] == [
        "First markdown body.",
        "Second markdown body.",
    ]
    assert records[0]["metadata"]["document_id"] == "doc_first"
    assert records[0]["metadata"]["chunk_id"] is None
    assert records[1]["metadata"]["chunk_id"] == "chunk_second"
    assert records[1]["metadata"]["source"] == "https://example.com/second"


def test_langchain_loader_reports_missing_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    monkeypatch.setitem(sys.modules, "langchain_core", None)
    monkeypatch.setitem(sys.modules, "langchain_core.documents", None)

    with pytest.raises(ImportError, match=r"pip install langchain-core"):
        DocpullPackLoader(pack).load()


def test_llamaindex_reader_reports_missing_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    monkeypatch.setitem(sys.modules, "llama_index", None)
    monkeypatch.setitem(sys.modules, "llama_index.core", None)
    monkeypatch.setitem(sys.modules, "llama_index.core.schema", None)

    with pytest.raises(ImportError, match=r"pip install llama-index-core"):
        DocpullPackReader(pack).load_data()


def test_integrations_package_exposes_lazy_exports() -> None:
    import docpull.integrations as integrations

    assert set(integrations.__all__) == {"DocpullPackLoader", "DocpullPackReader"}
    assert integrations.DocpullPackLoader is DocpullPackLoader
    assert integrations.DocpullPackReader is DocpullPackReader
    assert "DocpullPackLoader" in dir(integrations)
    with pytest.raises(AttributeError):
        integrations.missing_attribute  # noqa: B018


def test_langchain_loader_builds_real_documents(tmp_path: Path) -> None:
    pytest.importorskip("langchain_core")
    pack = tmp_path / "pack"
    write_context_pack(pack, records=_RECORDS)

    documents = DocpullPackLoader(pack).load()

    assert len(documents) == 2
    assert documents[0].page_content == "Parallel Search API returns cited JSON results."
    assert documents[0].metadata["document_id"] == "doc_search"
    assert documents[1].metadata["chunk_id"] == "chunk_a"


def test_llamaindex_reader_builds_real_documents(tmp_path: Path) -> None:
    pytest.importorskip("llama_index.core")
    pack = tmp_path / "pack"
    write_context_pack(pack, records=_RECORDS)

    documents = DocpullPackReader(pack).load_data()

    assert len(documents) == 2
    assert documents[0].text == "Parallel Search API returns cited JSON results."
    assert documents[0].metadata["document_id"] == "doc_search"
    assert documents[1].metadata["chunk_id"] == "chunk_a"

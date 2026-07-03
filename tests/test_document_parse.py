"""Tests for local document parsing into v3 packs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull.cli import main
from docpull.document_parse import DocumentParseError, parse_documents
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack


def test_parse_text_file_writes_v3_raw_pack(tmp_path: Path) -> None:
    source = tmp_path / "manual.md"
    source.write_text(
        "# Manual\n\n"
        "Alpha setup details for a local document parse workflow.\n\n"
        "Beta operational notes with enough words to become useful evidence.\n",
        encoding="utf-8",
    )
    pack_dir = tmp_path / "pack"

    result = parse_documents([source], pack_dir, backend="text", chunk_tokens=20)

    assert result["validation"]["status"] == "pass"
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
    assert (pack_dir / "documents.ndjson").exists()
    assert (pack_dir / "sources.md").exists()
    manifest = json.loads((pack_dir / "corpus.manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    assert manifest["output_format"] == "document-parse"
    assert manifest["records"][0]["output_path"].startswith("sources/001-manual.md")

    pack = load_pack(pack_dir)
    assert len(pack.documents) >= 1
    assert pack.documents[0].route["name"] == "local-document-parse"
    assert pack.documents[0].metadata["parse_backend"] == "text"
    assert pack.documents[0].rights["status"] == "unknown"
    assert pack.record_citation_id(pack.documents[0]) == "S1.1"


def test_parse_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("Local notes for CLI parsing.\n", encoding="utf-8")
    pack_dir = tmp_path / "pack"

    assert main(["parse", str(source), "-o", str(pack_dir), "--backend", "text", "--format", "json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == "document-parse"
    assert payload["validation"]["status"] == "pass"
    assert payload["artifacts"]["documents"] == "documents.ndjson"


def test_parse_cli_prepare_updates_result_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "agent-notes.txt"
    source.write_text("Agent-ready local notes for preparation.\n", encoding="utf-8")
    pack_dir = tmp_path / "pack"

    assert (
        main(
            [
                "parse",
                str(source),
                "-o",
                str(pack_dir),
                "--backend",
                "text",
                "--prepare",
                "--format",
                "json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    saved = json.loads((pack_dir / "parse.result.json").read_text(encoding="utf-8"))
    assert payload["prepared_level"] == "agent"
    assert payload["validation"]["level"] == "agent"
    assert saved["validation"]["level"] == "agent"
    assert (pack_dir / "context.lock.json").exists()


def test_parse_markitdown_backend_uses_optional_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "handbook.docx"
    source.write_bytes(b"fake docx bytes")
    calls: list[str] = []

    class FakeMarkItDown:
        def convert(self, path: str) -> object:
            calls.append(path)
            return SimpleNamespace(text_content="# Handbook\n\nConverted body.", title="Handbook")

    monkeypatch.setitem(sys.modules, "markitdown", SimpleNamespace(MarkItDown=FakeMarkItDown))

    result = parse_documents([source], tmp_path / "pack", backend="markitdown", emit_chunks=False)

    assert result["validation"]["status"] == "pass"
    assert calls == [str(source.resolve())]
    pack = load_pack(tmp_path / "pack")
    assert pack.documents[0].title == "Handbook"
    assert pack.documents[0].metadata["parse_backend"] == "markitdown"


def test_parse_markitdown_reports_missing_optional_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "handbook.docx"
    source.write_bytes(b"fake docx bytes")
    real_import_module = __import__("importlib").import_module

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "markitdown":
            raise ImportError("missing markitdown")
        return real_import_module(name, package)

    monkeypatch.setattr("docpull.document_parse.importlib.import_module", fake_import_module)

    with pytest.raises(DocumentParseError, match=r"docpull\[markitdown\]"):
        parse_documents([source], tmp_path / "pack", backend="markitdown")

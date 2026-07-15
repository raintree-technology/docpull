"""Tests for local document parsing into v3 packs."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull.cli import main
from docpull.document_parse import (
    DocumentParseError,
    parse_documents,
    parse_one_document,
    parse_remote_document_bytes,
)
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack


def _pdf_bytes(*, text: str | None = None, image_only: bool = False, encrypted: bool = False) -> bytes:
    pypdf = pytest.importorskip("pypdf")
    generic = pytest.importorskip("pypdf.generic")
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text is not None:
        font = generic.DictionaryObject(
            {
                generic.NameObject("/Type"): generic.NameObject("/Font"),
                generic.NameObject("/Subtype"): generic.NameObject("/Type1"),
                generic.NameObject("/BaseFont"): generic.NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        page[generic.NameObject("/Resources")] = generic.DictionaryObject(
            {
                generic.NameObject("/Font"): generic.DictionaryObject(
                    {generic.NameObject("/F1"): font_reference}
                )
            }
        )
        stream = generic.DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[generic.NameObject("/Contents")] = writer._add_object(stream)
    if image_only:
        image = generic.DecodedStreamObject()
        image.set_data(b"\x00")
        image.update(
            {
                generic.NameObject("/Type"): generic.NameObject("/XObject"),
                generic.NameObject("/Subtype"): generic.NameObject("/Image"),
                generic.NameObject("/Width"): generic.NumberObject(1),
                generic.NameObject("/Height"): generic.NumberObject(1),
                generic.NameObject("/ColorSpace"): generic.NameObject("/DeviceGray"),
                generic.NameObject("/BitsPerComponent"): generic.NumberObject(8),
            }
        )
        image_reference = writer._add_object(image)
        page[generic.NameObject("/Resources")] = generic.DictionaryObject(
            {
                generic.NameObject("/XObject"): generic.DictionaryObject(
                    {generic.NameObject("/Im1"): image_reference}
                )
            }
        )
    if encrypted:
        writer.encrypt("secret")
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


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


def test_parse_pdf_rejects_truncated_container_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "truncated.pdf"
    source.write_bytes(b"%PDF-1.7\ntruncated")
    monkeypatch.setattr(
        "docpull.document_parse._parse_markitdown",
        lambda _path: pytest.fail("backend must not receive a malformed PDF"),
    )

    with pytest.raises(DocumentParseError, match="Malformed or truncated"):
        parse_one_document(
            source,
            backend="markitdown",
            source_url=source.as_uri(),
            title=None,
        )


def test_parse_pdf_rejects_encrypted_container_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "encrypted.pdf"
    source.write_bytes(_pdf_bytes(encrypted=True))
    monkeypatch.setattr(
        "docpull.document_parse._parse_markitdown",
        lambda _path: pytest.fail("backend must not receive an encrypted PDF"),
    )

    with pytest.raises(DocumentParseError, match="Encrypted PDF"):
        parse_one_document(
            source,
            backend="markitdown",
            source_url=source.as_uri(),
            title=None,
        )


def test_parse_pdf_reports_image_only_ocr_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "image.pdf"
    source.write_bytes(_pdf_bytes(image_only=True))
    monkeypatch.setattr("docpull.document_parse._parse_markitdown", lambda _path: ("", {}))

    with pytest.raises(DocumentParseError, match="OCR backend"):
        parse_one_document(
            source,
            backend="markitdown",
            source_url=source.as_uri(),
            title=None,
        )


def test_parse_remote_pdf_uses_isolated_worker_and_records_provenance() -> None:
    body = _pdf_bytes(text="Paper Parsed body transformer machine translation")

    parsed = parse_remote_document_bytes(
        body,
        source_url="https://example.com/paper.pdf",
        content_type="application/pdf",
        backend="auto",
    )

    assert parsed.backend == "pypdf"
    assert "transformer machine translation" in parsed.content
    assert parsed.metadata["remote_source_retained"] is False
    assert parsed.metadata["source_bytes"] == len(body)
    assert parsed.metadata["source_sha256"]
    assert parsed.metadata["page_count"] == 1
    assert parsed.metadata["parser"] == "pypdf"
    assert parsed.metadata["token_count"] >= 6
    assert "fused_word_proxy_rate" in parsed.metadata


def test_remote_pdf_temporary_permissions_and_environment_are_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docpull.document_parse import (
        _prepare_remote_document,
        _remote_worker_command,
        _remote_worker_environment,
    )

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross-worker-boundary")
    request = _prepare_remote_document(
        _pdf_bytes(text="Private file"),
        source_url="https://example.com/private.pdf",
        content_type="application/pdf",
        backend="pypdf",
        timeout_seconds=60,
        memory_mib=1024,
    )
    with request:
        assert request.source_path.parent.stat().st_mode & 0o777 == 0o700
        assert request.source_path.stat().st_mode & 0o777 == 0o600
        assert request.request_path.stat().st_mode & 0o777 == 0o600
        completed = subprocess.run(
            _remote_worker_command(request),
            env=_remote_worker_environment(),
            check=False,
            capture_output=True,
        )
        assert completed.returncode == 0
        assert request.result_path.stat().st_mode & 0o777 == 0o600
    assert "OPENAI_API_KEY" not in _remote_worker_environment()
    assert _remote_worker_environment()["PYTHONUTF8"] == "1"


def test_parse_remote_pdf_rejects_signature_mismatch() -> None:
    with pytest.raises(DocumentParseError, match="PDF signature"):
        parse_remote_document_bytes(
            b"not-pdf",
            source_url="https://example.com/paper.pdf",
            content_type="application/pdf",
        )


def test_pdf_auto_fallback_order_only_advances_after_empty_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    calls: list[str] = []

    def pypdf_empty(_path: Path) -> tuple[str, dict[str, object]]:
        calls.append("pypdf")
        return "", {"page_count": 1, "image_count": 0}

    def markitdown_text(_path: Path) -> tuple[str, dict[str, object]]:
        calls.append("markitdown")
        return "Recovered text", {}

    monkeypatch.setattr("docpull.document_parse._parse_pypdf", pypdf_empty)
    monkeypatch.setattr("docpull.document_parse._parse_markitdown", markitdown_text)
    monkeypatch.setattr(
        "docpull.document_parse._parse_unstructured",
        lambda _path: pytest.fail("unstructured must not run after successful MarkItDown"),
    )

    parsed = parse_one_document(source, backend="auto", source_url=source.as_uri(), title=None)

    assert parsed.content == "Recovered text"
    assert calls == ["pypdf", "markitdown"]


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        (
            "Attention Is All You Need multi head attention machine translation",
            "multi head attention machine translation",
        ),
        (
            "Ray flexible distributed Python reinforcement learning applications",
            "distributed Python reinforcement learning",
        ),
    ],
)
def test_pypdf_recovers_audited_concept_phrases_without_fused_words(
    tmp_path: Path,
    phrase: str,
    expected: str,
) -> None:
    source = tmp_path / "concept.pdf"
    source.write_bytes(_pdf_bytes(text=phrase))

    parsed = parse_one_document(source, backend="pypdf", source_url=source.as_uri(), title=None)

    assert expected in parsed.content
    assert parsed.metadata["fused_word_proxy_count"] == 0


def test_remote_pdf_timeout_kills_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[int] = []

    class TimedOutProcess:
        pid = 4242
        returncode: int | None = None
        calls = 0

        def communicate(self, timeout: int | None = None) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("worker", timeout)
            self.returncode = -9
            return b"", b""

    monkeypatch.setattr("docpull.document_parse.subprocess.Popen", lambda *args, **kwargs: TimedOutProcess())
    monkeypatch.setattr("docpull.document_parse._terminate_process_group", killed.append)

    with pytest.raises(DocumentParseError, match="wall-time"):
        parse_remote_document_bytes(
            _pdf_bytes(text="timeout fixture"),
            source_url="https://example.com/timeout.pdf",
            content_type="application/pdf",
            backend="pypdf",
            timeout_seconds=1,
        )

    assert killed == [4242]


@pytest.mark.asyncio
async def test_remote_pdf_cancellation_kills_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docpull.document_parse import parse_remote_document_bytes_async

    killed: list[int] = []

    class WaitingProcess:
        pid = 4343
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.Future()
            return b"", b""

        async def wait(self) -> int:
            self.returncode = -9
            return -9

        def kill(self) -> None:
            self.returncode = -9

    async def create_process(*args: object, **kwargs: object) -> WaitingProcess:
        return WaitingProcess()

    monkeypatch.setattr("docpull.document_parse.asyncio.create_subprocess_exec", create_process)
    monkeypatch.setattr("docpull.document_parse._terminate_process_group", killed.append)
    task = asyncio.create_task(
        parse_remote_document_bytes_async(
            _pdf_bytes(text="cancellation fixture"),
            source_url="https://example.com/cancel.pdf",
            content_type="application/pdf",
            backend="pypdf",
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert killed == [4343]


def test_remote_pdf_input_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("docpull.document_parse.REMOTE_DOCUMENT_MAX_INPUT_BYTES", 8)

    with pytest.raises(DocumentParseError, match="50 MiB"):
        parse_remote_document_bytes(
            b"%PDF-1.7 too large",
            source_url="https://example.com/large.pdf",
            content_type="application/pdf",
        )


def test_remote_pdf_output_file_limit_is_enforced() -> None:
    from docpull.document_parse import (
        REMOTE_DOCUMENT_MAX_OUTPUT_BYTES,
        _prepare_remote_document,
        _read_remote_worker_result,
    )

    request = _prepare_remote_document(
        _pdf_bytes(text="output fixture"),
        source_url="https://example.com/output.pdf",
        content_type="application/pdf",
        backend="pypdf",
        timeout_seconds=60,
        memory_mib=1024,
    )
    with request:
        request.result_path.touch(mode=0o600)
        os.truncate(request.result_path, REMOTE_DOCUMENT_MAX_OUTPUT_BYTES + 1)
        with pytest.raises(DocumentParseError, match="100 MiB"):
            _read_remote_worker_result(request)


def test_posix_worker_resource_limits_are_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import resource

    from docpull.document_parse import _resource_limit_setup

    if os.name != "posix":
        pytest.skip("POSIX resource limits are not available")
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "setrlimit", lambda kind, limit: calls.append((kind, limit)))

    setup = _resource_limit_setup(7, 256)
    assert setup is not None
    setup()

    assert (resource.RLIMIT_CPU, (7, 7)) in calls
    if hasattr(resource, "RLIMIT_AS"):
        assert (resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024)) in calls

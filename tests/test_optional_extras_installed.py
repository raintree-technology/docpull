"""Happy-path smoke tests for installed local optional extras."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from docpull.cli import main
from docpull.conversion.chunking import TokenCounter
from docpull.conversion.trafilatura_extractor import TrafilaturaExtractor
from docpull.discovery.filters import normalize_url
from docpull.http.client import AsyncHttpClient
from docpull.http.rate_limiter import PerHostRateLimiter
from docpull.output_contract import validate_pack_contract


def _run_cli(args: list[str]) -> None:
    try:
        code = main(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 0, "docpull " + " ".join(args)


def test_installed_trafilatura_and_tiktoken_are_used() -> None:
    pytest.importorskip("trafilatura")
    tiktoken = pytest.importorskip("tiktoken")

    html = b"""
    <html><body><article>
      <h1>Optional Extraction</h1>
      <p>Trafilatura extracts this installed-extra evidence paragraph.</p>
    </article></body></html>
    """

    markdown = TrafilaturaExtractor().extract(html, "https://docs.example.com/optional")
    assert "installed-extra evidence" in markdown

    counter = TokenCounter()
    assert counter.exact is True
    assert counter.count("token aware local optional extra") == len(
        tiktoken.get_encoding("cl100k_base").encode("token aware local optional extra")
    )


@pytest.mark.asyncio
async def test_installed_proxy_and_url_normalize_extras_are_used() -> None:
    pytest.importorskip("aiohttp_socks")
    pytest.importorskip("url_normalize")

    normalized = normalize_url("HTTPS://Example.COM:443/docs/%7Euser/#section")
    assert normalized == "https://example.com/docs/~user"

    client = AsyncHttpClient(rate_limiter=PerHostRateLimiter(), proxy="socks5://127.0.0.1:1080")
    connector = client._build_connector(None)
    assert connector.__class__.__module__.startswith("aiohttp_socks")
    assert client._request_proxy is None
    close_result = connector.close()
    if inspect.isawaitable(close_result):
        await close_result


def test_installed_markitdown_and_unstructured_parse_backends(tmp_path: Path) -> None:
    pytest.importorskip("markitdown")
    pytest.importorskip("unstructured.partition.auto")

    markitdown_source = tmp_path / "markitdown.md"
    markitdown_source.write_text("# MarkItDown Smoke\n\nInstalled backend body.\n", encoding="utf-8")
    markitdown_pack = tmp_path / "markitdown-pack"
    _run_cli(
        [
            "parse",
            str(markitdown_source),
            "-o",
            str(markitdown_pack),
            "--backend",
            "markitdown",
            "--format",
            "json",
        ]
    )
    assert validate_pack_contract(markitdown_pack, level="raw")["status"] == "pass"
    markitdown_result = json.loads((markitdown_pack / "parse.result.json").read_text(encoding="utf-8"))
    assert markitdown_result["backend_count"] == {"markitdown": 1}

    unstructured_source = tmp_path / "unstructured.txt"
    unstructured_source.write_text("Unstructured Smoke\n\nInstalled backend body.\n", encoding="utf-8")
    unstructured_pack = tmp_path / "unstructured-pack"
    _run_cli(
        [
            "parse",
            str(unstructured_source),
            "-o",
            str(unstructured_pack),
            "--backend",
            "unstructured",
            "--format",
            "json",
        ]
    )
    assert validate_pack_contract(unstructured_pack, level="raw")["status"] == "pass"
    unstructured_result = json.loads((unstructured_pack / "parse.result.json").read_text(encoding="utf-8"))
    assert unstructured_result["backend_count"] == {"unstructured": 1}


def test_installed_presidio_redaction_uses_offline_analyzer(tmp_path: Path) -> None:
    pytest.importorskip("presidio_analyzer")

    source = tmp_path / "pii.txt"
    source.write_text("Contact Jane at jane@example.com or 415-555-0101.\n", encoding="utf-8")
    pack = tmp_path / "pack"
    redacted = tmp_path / "redacted"

    _run_cli(["parse", str(source), "-o", str(pack), "--backend", "text", "--format", "json"])
    _run_cli(["pack", "redact", str(pack), "-o", str(redacted), "--backend", "presidio"])

    report = json.loads((redacted / "redaction.report.json").read_text(encoding="utf-8"))
    assert report["backend"] == "presidio"
    assert report["match_count"] >= 2
    source_text = (redacted / "sources" / "001-pii.md").read_text(encoding="utf-8")
    assert "jane@example.com" not in source_text
    assert "415-555-0101" not in source_text


def test_installed_parquet_export_and_e2b_free_check(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("pyarrow")
    pytest.importorskip("e2b")

    source = tmp_path / "pack.txt"
    source.write_text("Parquet export body.\n", encoding="utf-8")
    pack = tmp_path / "pack"
    output = tmp_path / "pack.parquet"
    _run_cli(["parse", str(source), "-o", str(pack), "--backend", "text", "--format", "json"])
    _run_cli(["export", str(pack), "--format", "parquet", "-o", str(output)])
    assert output.exists()

    assert main(["render", "--check", "--runtime", "e2b"]) == 1
    assert "E2B Sandbox backend unavailable" in capsys.readouterr().out

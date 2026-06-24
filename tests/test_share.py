"""Tests for local report sharing."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from docpull.cli import main
from docpull.share import create_report_server, render_report_document, report_url, run_share_cli


def test_render_report_document_renders_markdown_safely(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "\n".join(
            [
                "# Evidence Report",
                "",
                "See [source](https://example.com/?a=1&b=2) and `hash_123`.",
                "",
                "<script>alert('x')</script>",
                "",
                "| Field | Value |",
                "| --- | --- |",
                "| Status | Ready |",
            ]
        ),
        encoding="utf-8",
    )

    html = render_report_document(report).decode("utf-8")

    assert "<h1>Evidence Report</h1>" in html
    assert '<a href="https://example.com/?a=1&amp;b=2" rel="noreferrer">source</a>' in html
    assert "<code>hash_123</code>" in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert "<th>Field</th>" in html
    assert "<td>Ready</td>" in html


def test_render_report_document_passes_full_html_through(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    report.write_text("<!doctype html><html><body><h1>Done</h1></body></html>", encoding="utf-8")

    html = render_report_document(report).decode("utf-8")

    assert html == "<!doctype html><html><body><h1>Done</h1></body></html>"


def test_create_report_server_serves_report_and_health(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("# Evidence\n\nLocal report.", encoding="utf-8")
    server = create_report_server(report, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = report_url("127.0.0.1", server.server_port)
        with urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
            csp = response.headers["Content-Security-Policy"]
        assert "<h1>Evidence</h1>" in body
        assert "default-src 'none'" in csp

        with urlopen(f"{url}health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["status"] == "ok"
        assert health["format"] == "markdown"
        assert health["report_path"] == str(report.resolve())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_share_cli_rejects_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = run_share_cli([str(tmp_path / "missing.md")])

    assert result == 1
    assert "Report file does not exist" in capsys.readouterr().out


def test_share_cli_rejects_non_localhost_bind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "report.md"
    report.write_text("# Report", encoding="utf-8")

    result = run_share_cli([str(report), "--host", "0.0.0.0"])

    assert result == 1
    assert "Refusing non-localhost bind host" in capsys.readouterr().out


def test_main_dispatches_share_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["share", "--help"])

    assert exc.value.code == 0
    assert "Serve a local Markdown or HTML report" in capsys.readouterr().out

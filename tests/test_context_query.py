"""Locked retrieval, response budgets, version isolation, and citation precision."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest

from docpull import ContextQueryError, query_context
from docpull import project as project_module
from docpull.cli import main
from docpull.context_query import serialize_query_result
from docpull.project import init_project, sync_project


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    documents: list[tuple[str, str | None]],
) -> Path:
    root = tmp_path / name
    init_project(name=name, source=f"https://{name}.example/docs", root=root)

    async def fetch(**kwargs: Any) -> dict[str, Any]:
        records = []
        for i, (content, version) in enumerate(documents):
            records.append(
                {
                    "document_id": f"doc_{i}",
                    "url": f"https://{name}.example/docs/{i}",
                    "title": "API Reference",
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                    "metadata": {"version": version, "docpull_project_source": kwargs["source"].name},
                }
            )
        return {"records": records, "errors": [], "skips": [], "stats": {}}

    monkeypatch.setattr(project_module, "_sync_source", fetch)
    sync_project(root=root, run_id=f"{name}_run")
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_query_across_locks_deduplicates_and_preserves_exact_citations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = (
        "# Guide\n\nCafé retry policy: wait 2 seconds.\n\n```python\nretry(2)\n\n# preserve blank line\n```\n"
    )
    a = _project(tmp_path, monkeypatch, "a", [(content, "1.0")])
    b = _project(tmp_path, monkeypatch, "b", [(content, "1.0")])
    before = {root: _files(root) for root in (a, b)}

    def no_network(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Query attempted network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    result = query_context("retry", project_roots=[b, a, a], max_tokens=10000)
    assert result["status"] == "ok"
    assert len(result["results"]) == 2
    assert len({item["text"] for item in result["results"]}) == 2
    for passage in result["results"]:
        assert len(passage["citations"]) == 2
        assert passage["omitted_citations"] == 0
        for citation in passage["citations"]:
            assert content[citation["char_start"] : citation["char_end"]] == passage["text"]
            assert citation["content_hash"] == hashlib.sha256(content.encode()).hexdigest()
            assert citation["line_start"] == content[: citation["char_start"]].count("\n") + 1
            assert citation["line_end"] == content[: citation["char_end"] - 1].count("\n") + 1
            assert citation["record_index"] == 1
            assert citation["pack_version"] == f"{citation['pack']}_run"
            assert citation["version"] == "1.0"
    assert any(p["text"].startswith("```python") and p["text"].endswith("```") for p in result["results"])
    assert result == query_context("retry", project_roots=[a, b], max_tokens=10000)
    assert {root: _files(root) for root in (a, b)} == before


def test_versions_are_exact_and_unknown_versions_never_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(
        tmp_path,
        monkeypatch,
        "versions",
        [
            ("Retry legacy: retry(1).", "1.0"),
            ("Retry current: retry(2).", "2.0"),
            ("Retry unknown: retry(3).", None),
        ],
    )
    result = query_context("retry", project_roots=[root], versions=["2.0"])
    assert [p["text"] for p in result["results"]] == ["Retry current: retry(2)."]
    for filters in ({"versions": ["2"]}, {"pack_versions": ["wrong_run"]}, {"sources": ["wrong_source"]}):
        missing = query_context("retry", project_roots=[root], **filters)
        assert missing["status"] == "insufficient_evidence"
        assert missing["reason"] == "no_matches"
    selected = query_context(
        "retry",
        project_roots=[root],
        pack_versions=["versions_run"],
        sources=["versions-example-docs"],
        versions=["1.0"],
    )
    assert len(selected["results"]) == 1


@pytest.mark.parametrize("budget", [256, 700, 1200, 2500, 10000])
def test_budget_includes_citations_unicode_and_response_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget: int,
) -> None:
    code = "```python\n" + "retry('東京😀')\n" * 120 + "```"
    root = _project(tmp_path, monkeypatch, "budget", [("Retry safely.\n\n" + code, "1.0")])
    result = query_context("retry", project_roots=[root], max_tokens=budget)
    size = len(serialize_query_result(result).encode("utf-8"))
    assert size == result["budget"]["used_tokens"]
    assert size <= budget
    assert all(p["text"] in {"Retry safely.", code} for p in result["results"])
    if budget == 256:
        assert result["reason"] == "budget_exhausted"
        assert result["results"] == []


def test_query_reports_omitted_duplicate_citations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = [
        _project(tmp_path, monkeypatch, name, [("Retry safely.", "1.0")]) for name in ("one", "two", "three")
    ]
    result = query_context("retry", project_roots=roots, max_tokens=1100)
    assert len(result["results"]) == 1
    passage = result["results"][0]
    assert len(passage["citations"]) + passage["omitted_citations"] == 3
    assert passage["omitted_citations"] > 0


def test_query_uses_pinned_archive_not_mutable_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, monkeypatch, "pinned", [("Retry the pinned API.", "1.0")])
    (root / ".docpull/runs/pinned_run/documents.ndjson").write_text("tampered local content")
    (root / ".docpull/latest-run").write_text("unlocked_newer_run")
    result = query_context("retry", project_roots=[root])
    assert result["results"][0]["text"] == "Retry the pinned API."


def test_query_transferred_lock_without_installed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _project(tmp_path, monkeypatch, "original", [("Retry offline.", "2.0")])
    fresh = tmp_path / "fresh"
    (fresh / ".docpull").mkdir(parents=True)
    for name in ("docpull.yaml", ".docpull/context.lock.json"):
        shutil.copyfile(original / name, fresh / name)
    before = _files(fresh)
    expected = query_context("retry", project_roots=[original])
    assert (
        query_context("retry", project_roots=[fresh], artifact_dirs=[original / ".docpull/artifacts"])
        == expected
    )
    assert _files(fresh) == before


def test_query_keeps_code_indentation_distinct_and_applies_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = ["```python\nretry()\n```", "```python\n    retry()\n```"]
    root = _project(tmp_path, monkeypatch, "code", [("\n\n".join(blocks), None)])
    result = query_context("retry", project_roots=[root], max_tokens=10000)
    assert {p["text"] for p in result["results"]} == set(blocks)
    limited = query_context("retry", project_roots=[root], max_tokens=10000, limit=1)
    assert limited["results"] == result["results"][:1]
    assert limited["omitted_passages"] == 1


@pytest.mark.parametrize("damage", ["missing", "tampered", "lock"])
def test_query_fails_closed_without_partial_results_or_project_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    a = _project(tmp_path, monkeypatch, "valid", [("Retry valid.", "1.0")])
    b = _project(tmp_path, monkeypatch, "invalid", [("Retry invalid.", "1.0")])
    archive = next((b / ".docpull/artifacts").glob("*.zip"))
    if damage == "missing":
        archive.unlink()
    elif damage == "tampered":
        archive.write_bytes(b"tampered archive")
    else:
        lock_path = b / ".docpull/context.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["run_id"] = "changed_run"
        lock_path.write_text(json.dumps(lock))
    before = _files(tmp_path)
    with pytest.raises(ContextQueryError, match="artifact|lockfile"):
        query_context("retry", project_roots=[a, b])
    assert _files(tmp_path) == before


def test_query_cli_sdk_and_mcp_return_same_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _project(tmp_path, monkeypatch, "surfaces", [("Retry uses exponential backoff.", "2.0")])
    expected = query_context("retry", project_roots=[root], versions=["2.0"], max_tokens=2000)
    monkeypatch.chdir(root)
    assert main(["query", "retry", "--version", "2.0", "--max-tokens", "2000"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    import asyncio

    from docpull.mcp.server import _dispatch_tool

    result = asyncio.run(
        _dispatch_tool(
            "context_query",
            {
                "query": "retry",
                "project_roots": [str(root)],
                "versions": ["2.0"],
                "max_tokens": 2000,
            },
        )
    )
    assert not result.is_error
    assert result.data == expected


def test_no_match_and_invalid_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, monkeypatch, "empty", [("Retry carefully.", "1.0")])
    assert query_context("nonexistent", project_roots=[root])["reason"] == "no_matches"
    for options in (
        {"max_tokens": True},
        {"max_tokens": 0},
        {"limit": 0},
        {"versions": "1.0"},
        {"project_roots": []},
    ):
        with pytest.raises(ContextQueryError):
            query_context("retry", **options)
    with pytest.raises(ContextQueryError):
        query_context("  ", project_roots=[root])


@pytest.mark.asyncio
async def test_context_query_stdio_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import sys

    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from tests.test_mcp_server import _stdio_client_with_cleanup

    root = await asyncio.to_thread(_project, tmp_path, monkeypatch, "stdio", [("Retry with backoff.", "2.0")])
    expected = query_context("retry", project_roots=[root], versions=["2.0"])
    assert expected["results"][0]["text"] == "Retry with backoff."
    server = StdioServerParameters(command=sys.executable, args=["-m", "docpull", "mcp"])
    async with _stdio_client_with_cleanup(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        tool = next(t for t in tools.tools if t.name == "context_query")
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is False
        assert tool.inputSchema["properties"]["max_tokens"]["minimum"] == 256
        result = await session.call_tool(
            "context_query", {"query": "retry", "project_roots": [str(root)], "versions": ["2.0"]}
        )
        assert result.isError is False
        assert result.structuredContent == expected
        missing = await session.call_tool(
            "context_query", {"query": "retry", "project_roots": [str(tmp_path / "missing")]}
        )
        assert missing.isError is True

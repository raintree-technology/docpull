"""Tests for cited research answers and the compatible answer routes."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web

from docpull import research as research_module
from docpull.compat_api import CompatASGIApp
from docpull.research import (
    LlmConfig,
    ResearchError,
    _check_llm,
    answer_from_search,
    rank_evidence,
    run_research_cli,
)
from docpull.web_search import SearchHit, SearchResponse
from tests.test_compat_api import _post

ALPHA = "# Rate limits\n\nThe API allows 100 requests per minute per key.\n\nUnrelated footer text.\n"
BETA = "Intro.\n\nExceeding the rate limit returns HTTP 429 with a Retry-After header.\n"


def _search(*, beta_error: str | None = None) -> SearchResponse:
    return SearchResponse(
        query="rate limit per minute",
        backend="fake",
        hits=[
            SearchHit(url="https://docs.example.com/limits", title="Limits", content=ALPHA),
            SearchHit(url="https://empty.example.com", title="Empty", content="Nothing relevant here."),
            SearchHit(
                url="https://blog.example.com/429",
                title="429",
                content=None if beta_error else BETA,
                content_error=beta_error,
            ),
        ],
    )


def test_rank_evidence_keeps_exact_spans_and_dense_source_ids() -> None:
    evidence = rank_evidence("rate limit per minute", _search().hits)

    assert {item.source_id for item in evidence} == {"S1", "S2"}
    assert all(item.url != "https://empty.example.com" for item in evidence)
    for item in evidence:
        source = ALPHA if item.url.endswith("/limits") else BETA
        assert source[item.char_start : item.char_end] == item.text
    with pytest.raises(ResearchError, match="no searchable terms"):
        rank_evidence("the a of", _search().hits)


@pytest.mark.asyncio
async def test_extractive_answer_cites_every_passage() -> None:
    result = await answer_from_search("rate limit per minute", _search())

    assert result.mode == "extractive"
    assert all(line.rstrip().endswith(("[S1]", "[S2]")) for line in result.answer.splitlines())
    assert [source["id"] for source in result.sources] == ["S1", "S2"]

    partial = await answer_from_search("rate limit", _search(beta_error="HTTP 404"))
    assert any("could not be fetched" in warning for warning in partial.warnings)

    empty = await answer_from_search("zyzzyva", _search())
    assert empty.answer == "" and empty.sources == []
    assert "No fetched passage matched the question" in empty.warnings


@pytest.fixture
async def fake_model():
    replies: list[str] = []
    requests: list[dict[str, Any]] = []

    async def completions(request: web.Request) -> web.Response:
        requests.append(await request.json())
        return web.json_response({"choices": [{"message": {"content": replies.pop(0)}}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    yield LlmConfig(f"http://127.0.0.1:{port}/v1", "local-model"), replies, requests
    await runner.cleanup()


@pytest.mark.asyncio
async def test_model_answer_is_kept_only_with_valid_citations(fake_model: Any) -> None:
    llm, replies, requests = fake_model
    replies.extend(
        [
            "The limit is 100 requests per minute [S1]; excess returns 429 [S2].",
            "The limit is 100 per minute [S9].",
            "The limit is 100 per minute.",
        ]
    )

    good = await answer_from_search("rate limit per minute", _search(), llm=llm)
    assert good.mode == "synthesized" and "[S2]" in good.answer
    assert requests[0]["model"] == "local-model" and requests[0]["temperature"] == 0
    assert "[S1] Limits" in requests[0]["messages"][1]["content"]

    unknown = await answer_from_search("rate limit per minute", _search(), llm=llm)
    assert unknown.mode == "extractive"
    assert any("unknown sources: S9" in warning for warning in unknown.warnings)

    uncited = await answer_from_search("rate limit per minute", _search(), llm=llm)
    assert uncited.mode == "extractive"
    assert any("cites no sources" in warning for warning in uncited.warnings)


def test_remote_model_endpoints_require_opt_in() -> None:
    _check_llm(LlmConfig("http://localhost:11434/v1", "m"))
    with pytest.raises(ResearchError, match="allow-remote-llm"):
        _check_llm(LlmConfig("https://api.example.com/v1", "m"))
    _check_llm(LlmConfig("https://api.example.com/v1", "m", allow_remote=True))
    with pytest.raises(ResearchError, match="http"):
        _check_llm(LlmConfig("file:///etc/passwd", "m"))


@pytest.mark.asyncio
async def test_research_cli_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def fake_search_web(query: str, **kwargs: Any) -> SearchResponse:
        assert kwargs["include_content"] is True
        return _search()

    monkeypatch.setattr(research_module, "search_web", fake_search_web)
    argv = ["rate limit per minute", "--backend", "marginalia", "--json"]
    assert await asyncio.to_thread(run_research_cli, argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "extractive"
    assert payload["sources"][0]["id"] == "S1"
    assert payload["search"] == {"backend": "fake", "license": None}

    assert await asyncio.to_thread(run_research_cli, [*argv[:-1], "--llm-url", "http://localhost:1/v1"]) == 1


async def _search_fn(query: str, max_results: int, include: list[str], exclude: list[str], content: bool):
    assert content is True
    return _search()


@pytest.mark.asyncio
async def test_exa_answer_and_tavily_include_answer_shapes() -> None:
    app = CompatASGIApp(search=_search_fn)

    status, exa = await _post(app, "/answer", {"query": "rate limit per minute", "text": True})
    assert status == 200
    assert "[S1]" in exa["answer"]
    assert exa["citations"][0]["url"] == "https://docs.example.com/limits"
    assert "100 requests per minute" in exa["citations"][0]["text"]

    status, tavily = await _post(app, "/search", {"query": "rate limit per minute", "include_answer": True})
    assert status == 200
    assert "[S1]" in tavily["answer"]
    assert tavily["results"][0]["raw_content"] is None

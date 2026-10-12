"""Cited answers built from web search, local fetching, and passage ranking.

``research`` searches with a backend the caller names, fetches the top results
through the local pipeline, and ranks paragraphs with the same scoring as
``docpull query``. By default the answer is the best passages, each cited. An
OpenAI-compatible model can rewrite them into prose, but only if every citation
it returns points at a real source; otherwise the cited passages are returned.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .context_query import _STOPWORDS, _spans
from .pack_tools import _search_score, _term_count
from .web_search import BACKENDS, SearchHit, SearchResponse, WebSearchError, search_web

MAX_SOURCES = 10
MAX_PASSAGES_PER_SOURCE = 3
PASSAGE_CHARS = 600
_CITATION_RE = re.compile(r"\[S(\d+)\]")
_LLM_TIMEOUT = aiohttp.ClientTimeout(total=90)


class ResearchError(RuntimeError):
    """Research could not run with the given configuration."""


@dataclass(frozen=True)
class LlmConfig:
    """An OpenAI-compatible chat completions endpoint, such as Ollama or llama.cpp."""

    base_url: str
    model: str
    api_key: str | None = None
    allow_remote: bool = False


@dataclass(frozen=True)
class Evidence:
    source_id: str
    url: str
    title: str
    text: str
    char_start: int
    char_end: int
    score: int


@dataclass(frozen=True)
class ResearchResult:
    query: str
    answer: str
    mode: str
    sources: list[dict[str, str]]
    evidence: list[Evidence]
    search: SearchResponse
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["search"] = {"backend": self.search.backend, "license": self.search.license}
        return payload


async def research(
    query: str,
    *,
    backend: str,
    max_sources: int = 5,
    max_passages: int = 8,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    searxng_url: str | None = None,
    marginalia_key: str | None = None,
    llm: LlmConfig | None = None,
) -> ResearchResult:
    if not 1 <= max_sources <= MAX_SOURCES:
        raise ResearchError(f"max_sources must be between 1 and {MAX_SOURCES}")
    if llm is not None:
        _check_llm(llm)
    try:
        search = await search_web(
            query,
            backend=backend,
            max_results=max_sources,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            include_content=True,
            searxng_url=searxng_url,
            marginalia_key=marginalia_key,
        )
    except WebSearchError as err:
        raise ResearchError(str(err)) from err
    return await answer_from_search(query, search, max_passages=max_passages, llm=llm)


async def answer_from_search(
    query: str,
    search: SearchResponse,
    *,
    max_passages: int = 8,
    llm: LlmConfig | None = None,
) -> ResearchResult:
    """Rank fetched search results into cited evidence and an answer."""
    warnings = list(search.warnings)
    failed = [hit.url for hit in search.hits if hit.content_error]
    if failed:
        warnings.append(f"{len(failed)} result(s) could not be fetched and were skipped")
    evidence = rank_evidence(query, search.hits, max_passages=max_passages)
    first = {item.source_id: item for item in reversed(evidence)}
    sources = [
        {"id": source_id, "url": first[source_id].url, "title": first[source_id].title}
        for source_id in dict.fromkeys(item.source_id for item in evidence)
    ]
    if not evidence:
        warnings.append("No fetched passage matched the question")
        return ResearchResult(search.query, "", "extractive", [], [], search, warnings)

    extractive = _extractive_answer(evidence)
    if llm is None:
        return ResearchResult(search.query, extractive, "extractive", sources, evidence, search, warnings)
    try:
        answer = await _synthesize(query, evidence, llm)
    except ResearchError as err:
        warnings.append(f"Model answer discarded: {err}")
        return ResearchResult(search.query, extractive, "extractive", sources, evidence, search, warnings)
    return ResearchResult(search.query, answer, "synthesized", sources, evidence, search, warnings)


def rank_evidence(query: str, hits: list[SearchHit], *, max_passages: int = 8) -> list[Evidence]:
    """Score paragraphs across fetched hits; source IDs follow search rank."""
    terms = sorted(set(re.findall(r"[^\W_]+(?:[_-][^\W_]+)*", query.casefold())) - _STOPWORDS)
    if not terms:
        raise ResearchError("The question has no searchable terms")
    candidates: list[tuple[int, int, Evidence]] = []
    for rank, hit in enumerate(hits, start=1):
        content = hit.content or ""
        scored: list[Evidence] = []
        for start, end in _spans(content):
            raw = content[start:end]
            start += len(raw) - len(raw.lstrip())
            end -= len(raw) - len(raw.rstrip())
            text = content[start:end]
            if not text or not any(_term_count(text, term) for term in terms):
                continue
            score, _ = _search_score(
                query_terms=terms,
                phrase=query.strip().lower(),
                title=hit.title,
                heading="",
                url=hit.url,
                content=text,
            )
            scored.append(Evidence(f"S{rank}", hit.url, hit.title, text, start, end, score))
        scored.sort(key=lambda item: (-item.score, item.char_start))
        candidates.extend((item.score, rank, item) for item in scored[:MAX_PASSAGES_PER_SOURCE])
    candidates.sort(key=lambda entry: (-entry[0], entry[1], entry[2].char_start))
    chosen = [item for _, _, item in candidates[:max_passages]]
    # Renumber sources densely in order of first use so citations read S1, S2, ...
    renumber = {
        old: f"S{index}" for index, old in enumerate(dict.fromkeys(e.source_id for e in chosen), start=1)
    }
    return [
        Evidence(renumber[e.source_id], e.url, e.title, e.text, e.char_start, e.char_end, e.score)
        for e in chosen
    ]


def _extractive_answer(evidence: list[Evidence]) -> str:
    lines = []
    for item in evidence:
        text = " ".join(item.text.split())
        if len(text) > PASSAGE_CHARS:
            text = text[: PASSAGE_CHARS - 1].rsplit(" ", 1)[0] + "…"
        lines.append(f"- {text} [{item.source_id}]")
    return "\n".join(lines)


def _check_llm(llm: LlmConfig) -> None:
    parts = urlsplit(llm.base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ResearchError("llm base URL must be an http(s) URL")
    if not llm.model.strip():
        raise ResearchError("llm model must not be empty")
    if not llm.allow_remote and not _is_loopback(parts.hostname):
        raise ResearchError("A non-localhost model endpoint requires --allow-remote-llm; it may cost money")


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


async def _synthesize(query: str, evidence: list[Evidence], llm: LlmConfig) -> str:
    numbered = "\n\n".join(f"[{item.source_id}] {item.title} ({item.url})\n{item.text}" for item in evidence)
    messages = [
        {
            "role": "system",
            "content": (
                "Answer only from the numbered evidence. Cite every claim with its source tag, "
                "for example [S1]. If the evidence does not answer the question, say so."
            ),
        },
        {"role": "user", "content": f"Question: {query}\n\nEvidence:\n{numbered}"},
    ]
    headers = {"Authorization": f"Bearer {llm.api_key}"} if llm.api_key else {}
    url = llm.base_url.rstrip("/") + "/chat/completions"
    body = {"model": llm.model, "messages": messages, "temperature": 0, "max_tokens": 800}
    try:
        async with (
            aiohttp.ClientSession(timeout=_LLM_TIMEOUT) as session,
            session.post(url, json=body, headers=headers) as response,
        ):
            if response.status >= 400:
                raise ResearchError(f"model endpoint returned HTTP {response.status}")
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as err:
        raise ResearchError(f"model request failed: {type(err).__name__}") from err
    try:
        answer = str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as err:
        raise ResearchError("model returned no answer") from err
    valid = {item.source_id for item in evidence}
    cited = {f"S{number}" for number in _CITATION_RE.findall(answer)}
    if not cited:
        raise ResearchError("answer cites no sources")
    if not cited <= valid:
        raise ResearchError(f"answer cites unknown sources: {', '.join(sorted(cited - valid))}")
    return answer


def create_research_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull research",
        description="Answer a question with cited passages from web search and local fetching",
    )
    parser.add_argument("question")
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--max-sources", type=int, default=5)
    parser.add_argument("--max-passages", type=int, default=8)
    parser.add_argument("--include-domain", action="append", default=[], dest="include_domains")
    parser.add_argument("--exclude-domain", action="append", default=[], dest="exclude_domains")
    parser.add_argument("--searxng-url")
    parser.add_argument("--llm-url", help="OpenAI-compatible base URL, e.g. http://localhost:11434/v1")
    parser.add_argument("--llm-model", help="Model name for --llm-url")
    parser.add_argument(
        "--llm-api-key-env", metavar="NAME", help="Environment variable holding the model key"
    )
    parser.add_argument(
        "--allow-remote-llm",
        action="store_true",
        help="Allow a non-localhost model endpoint, which may cost money",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def llm_config_from_args(args: argparse.Namespace) -> LlmConfig | None:
    if not args.llm_url and not args.llm_model:
        return None
    if not (args.llm_url and args.llm_model):
        raise ResearchError("--llm-url and --llm-model must be given together")
    api_key = os.environ.get(args.llm_api_key_env) if args.llm_api_key_env else None
    if args.llm_api_key_env and not api_key:
        raise ResearchError(f"Environment variable {args.llm_api_key_env} is empty or unset")
    return LlmConfig(args.llm_url, args.llm_model, api_key=api_key, allow_remote=args.allow_remote_llm)


def run_research_cli(argv: list[str] | None = None) -> int:
    args = create_research_parser().parse_args(argv)
    try:
        result = asyncio.run(
            research(
                args.question,
                backend=args.backend,
                max_sources=args.max_sources,
                max_passages=args.max_passages,
                include_domains=args.include_domains,
                exclude_domains=args.exclude_domains,
                searxng_url=args.searxng_url,
                llm=llm_config_from_args(args),
            )
        )
    except ResearchError as err:
        print(f"docpull research: {err}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(result.answer or "No answer: no fetched passage matched the question.")
    if result.sources:
        print("\nSources:")
        for source in result.sources:
            print(f"[{source['id']}] {source['title'] or source['url']} — {source['url']}")
    if result.search.license:
        print(f"\nSearch results license: {result.search.license}")
    return 0

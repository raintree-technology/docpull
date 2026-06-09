"""LLM-judge dimension for docpull benchmark packs (stub).

The existing ``benchmark_score`` is fully deterministic. For research-style
evals the Anthropic "Demystifying evals for AI agents" post recommends
corroborating heuristics with a model-based rubric, calibrated against
human judgment on a small set of targets. This module provides that
corroborating signal as an **advisory** score (parallel to, not folded
into, ``benchmark_score``) so the heuristic baseline and its regression
properties stay intact.

What this gives you:
    * A clear rubric prompt (coverage / groundedness / source_authority /
      synthesis_readiness) the model fills in with per-dimension scores.
    * A key-gated transport: if ``ANTHROPIC_API_KEY`` is unset, the judge
      returns ``skipped=True`` with a structured reason rather than failing
      the run.
    * A pluggable ``client`` callable so the same code path works in tests
      (inject a stub) and in CI (skip cleanly).
    * Document sampling capped to keep cost predictable and reproducible.

Wire-up checklist (deferred — this is a stub, not a calibrated grader):
    * Calibrate the rubric against ~10 hand-graded packs (see Anthropic eval
      post, "Design graders thoughtfully" section).
    * Decide reporting cadence (every run vs. a sampled subset) given cost.
    * Optionally add a self-consistency layer (multi-judge consensus).

Usage:
    python -m docpull.judge .bench/runs/<id>/<target>/<workflow>/run-1 \\
        --task-prompt "Build a context pack for the Parallel API docs"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

JUDGE_SCHEMA_VERSION = 1
JUDGE_MODEL_ENV = "DOCPULL_JUDGE_MODEL"
JUDGE_API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_JUDGE_MODEL = "claude-opus-4-7"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

JUDGE_DIMENSIONS = ("coverage", "groundedness", "source_authority", "synthesis_readiness")
JUDGE_WEIGHTS = {
    "coverage": 0.35,
    "groundedness": 0.25,
    "source_authority": 0.20,
    "synthesis_readiness": 0.20,
}
MAX_SAMPLED_DOCS = 6
MAX_DOC_CHARS = 1500
JUDGE_REQUEST_TIMEOUT_S = 60.0

JudgeClient = Callable[[str], str]


RUBRIC_PROMPT = """You are grading a documentation context pack produced by an automated
agent. The pack is a collection of Markdown documents intended for a downstream
LLM agent to consult when answering questions or writing code about the topic.

Task prompt (what the agent was asked to build a pack for):
{task_prompt}

Expected authoritative domains (if relevant):
{expected_domains}

Sample of {sampled} of {total} document(s) in the pack (truncated):
{documents}

Score the pack from 0-100 on each of these four dimensions, then explain
briefly. If you do not have enough evidence to judge a dimension, return
"Unknown" for that dimension's score and say why in the rationale — do not
guess.

Dimensions:
- coverage: does the pack cover the intent of the task prompt across the
  breadth a useful answer would need? Penalize narrow / one-topic packs when
  the task implies breadth.
- groundedness: are the documents factually relevant to the topic and free
  of obvious irrelevant noise (navigation, ads, unrelated pages)?
- source_authority: do the documents come from the canonical/expected
  authoritative sources, not tangential third-party pages?
- synthesis_readiness: is the content shaped well for downstream agent use
  — coherent prose, code blocks intact, no paywall snippets, no broken HTML?

Return ONLY a JSON object on a single line, no prose before or after:
{{"coverage": {{"score": <0-100 or "Unknown">, "rationale": "<one sentence>"}},
 "groundedness": {{"score": <0-100 or "Unknown">, "rationale": "<one sentence>"}},
 "source_authority": {{"score": <0-100 or "Unknown">, "rationale": "<one sentence>"}},
 "synthesis_readiness": {{"score": <0-100 or "Unknown">, "rationale": "<one sentence>"}}}}
"""


def judge_pack(
    pack_dir: Path,
    task: dict[str, Any],
    *,
    client: JudgeClient | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Grade a pack with an LLM rubric. Returns an advisory ``judge_score`` dict.

    ``task`` is expected to carry: ``prompt`` (str), optionally
    ``expected_domains`` (list[str]). The returned dict mirrors the shape of
    ``benchmark_score`` (schema_version / score / grade / weights /
    dimensions) plus ``skipped`` / ``skip_reason`` / ``model`` /
    ``doc_sample_count`` so callers can render it alongside the heuristic
    score without special casing.
    """
    documents_path = pack_dir / "documents.ndjson"
    if not documents_path.exists():
        return _skipped("documents.ndjson missing", model=model)

    docs = list(_read_documents(documents_path))
    if not docs:
        return _skipped("pack has zero documents", model=model)

    resolved_client, resolved_model, key_error = _resolve_client(client=client, model=model)
    if resolved_client is None:
        return _skipped(key_error or "no judge client configured", model=resolved_model)

    sampled = docs[:MAX_SAMPLED_DOCS]
    prompt = RUBRIC_PROMPT.format(
        task_prompt=task.get("prompt") or "(none provided)",
        expected_domains=", ".join(task.get("expected_domains") or []) or "(none provided)",
        sampled=len(sampled),
        total=len(docs),
        documents=_format_documents(sampled),
    )
    try:
        raw = resolved_client(prompt)
    except _JudgeTransportError as exc:
        return _skipped(f"judge transport error: {exc}", model=resolved_model)

    parsed = _parse_judgment(raw)
    if parsed is None:
        return _skipped("judge returned non-JSON response", model=resolved_model)

    dimensions, score = _aggregate(parsed)
    return {
        "schema_version": JUDGE_SCHEMA_VERSION,
        "score": score,
        "grade": _grade(score) if score is not None else None,
        "weights": JUDGE_WEIGHTS,
        "dimensions": dimensions,
        "skipped": False,
        "skip_reason": None,
        "model": resolved_model,
        "doc_sample_count": len(sampled),
    }


def _read_documents(path: Path) -> Iterable[dict[str, Any]]:
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _format_documents(docs: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for i, doc in enumerate(docs, start=1):
        content = str(doc.get("content") or "")
        if len(content) > MAX_DOC_CHARS:
            content = content[:MAX_DOC_CHARS] + " …"
        chunks.append(
            f"--- doc {i} ---\n"
            f"url: {doc.get('url')}\n"
            f"title: {doc.get('title')}\n\n"
            f"{content}"
        )
    return "\n\n".join(chunks)


def _resolve_client(
    *,
    client: JudgeClient | None,
    model: str | None,
) -> tuple[JudgeClient | None, str | None, str | None]:
    resolved_model = model or os.environ.get(JUDGE_MODEL_ENV) or DEFAULT_JUDGE_MODEL
    if client is not None:
        return client, resolved_model, None
    api_key = os.environ.get(JUDGE_API_KEY_ENV)
    if not api_key:
        return None, resolved_model, f"{JUDGE_API_KEY_ENV} not set"
    return _AnthropicMessagesClient(api_key=api_key, model=resolved_model), resolved_model, None


def _parse_judgment(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _aggregate(parsed: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    dimensions: dict[str, Any] = {}
    weighted_total = 0.0
    weight_used = 0.0
    for name in JUDGE_DIMENSIONS:
        weight = JUDGE_WEIGHTS[name]
        entry = parsed.get(name) if isinstance(parsed.get(name), dict) else {}
        raw_score = entry.get("score") if isinstance(entry, dict) else None
        rationale = entry.get("rationale") if isinstance(entry, dict) else None
        numeric: int | None
        if isinstance(raw_score, int | float) and not isinstance(raw_score, bool):
            numeric = max(0, min(100, int(raw_score)))
            weighted_total += numeric * weight
            weight_used += weight
        else:
            numeric = None
        dimensions[name] = {
            "score": numeric,
            "weight": weight,
            "rationale": rationale if isinstance(rationale, str) else None,
        }
    score = round(weighted_total / weight_used) if weight_used > 0 else None
    return dimensions, score


def _grade(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "fair"
    if score >= 40:
        return "poor"
    return "failing"


def _skipped(reason: str, *, model: str | None) -> dict[str, Any]:
    return {
        "schema_version": JUDGE_SCHEMA_VERSION,
        "score": None,
        "grade": None,
        "weights": JUDGE_WEIGHTS,
        "dimensions": {
            name: {"score": None, "weight": JUDGE_WEIGHTS[name], "rationale": None}
            for name in JUDGE_DIMENSIONS
        },
        "skipped": True,
        "skip_reason": reason,
        "model": model,
        "doc_sample_count": 0,
    }


class _JudgeTransportError(RuntimeError):
    """Raised when the judge HTTP call fails; converted to skipped result."""


class _AnthropicMessagesClient:
    """Minimal urllib-based client for the Anthropic Messages API.

    Kept dependency-free on purpose (no anthropic SDK, no httpx). The Anthropic
    eval post calls out that LLM judges benefit from a clean retry/timeout
    surface and easy injection in tests — this class is the seam.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def __call__(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self._model,
                "max_tokens": 600,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        request = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=JUDGE_REQUEST_TIMEOUT_S) as response:  # noqa: S310
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise _JudgeTransportError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise _JudgeTransportError(str(exc.reason)) from exc
        except json.JSONDecodeError as exc:
            raise _JudgeTransportError("response was not JSON") from exc
        content = payload.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                return text
        raise _JudgeTransportError("response missing content[0].text")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pack_dir",
        type=Path,
        help="path to a pack output dir (containing documents.ndjson)",
    )
    parser.add_argument("--task-prompt", required=True, help="the prompt the pack was built for")
    parser.add_argument(
        "--expected-domain",
        action="append",
        default=[],
        help="authoritative domain(s); repeatable",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"judge model id (default: ${JUDGE_MODEL_ENV} or {DEFAULT_JUDGE_MODEL})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = judge_pack(
        args.pack_dir,
        {"prompt": args.task_prompt, "expected_domains": args.expected_domain},
        model=args.model,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if not result["skipped"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

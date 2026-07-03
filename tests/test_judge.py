"""Tests for the advisory private benchmark judge.

We never call out to the real Anthropic API in tests. The judge accepts a
``client`` callable so we inject canned responses; the default code path
should also skip cleanly when no API key is set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docpull.judge import (
    JUDGE_API_KEY_ENV,
    JUDGE_DIMENSIONS,
    _parse_judgment,
    judge_pack,
)


def _write_pack(pack_dir: Path, docs: list[dict]) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "documents.ndjson").write_text("\n".join(json.dumps(d) for d in docs) + "\n")


def test_skips_when_no_api_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(JUDGE_API_KEY_ENV, raising=False)
    _write_pack(tmp_path, [{"url": "https://x", "title": "t", "content": "c"}])

    result = judge_pack(tmp_path, {"prompt": "build a pack"})

    assert result["skipped"] is True
    assert result["skip_reason"] == f"{JUDGE_API_KEY_ENV} not set"
    assert result["score"] is None
    for dim in JUDGE_DIMENSIONS:
        assert result["dimensions"][dim]["score"] is None


def test_skips_when_pack_missing(tmp_path: Path) -> None:
    result = judge_pack(tmp_path, {"prompt": "build a pack"}, client=lambda _p: "{}")
    assert result["skipped"] is True
    assert "documents.ndjson missing" in result["skip_reason"]


def test_skips_when_pack_empty(tmp_path: Path) -> None:
    (tmp_path / "documents.ndjson").write_text("")
    result = judge_pack(tmp_path, {"prompt": "p"}, client=lambda _p: "{}")
    assert result["skipped"] is True
    assert "zero documents" in result["skip_reason"]


def test_parses_judge_response(tmp_path: Path) -> None:
    _write_pack(
        tmp_path,
        [{"url": f"https://docs.example.com/{i}", "title": f"t{i}", "content": "body"} for i in range(3)],
    )
    response = json.dumps(
        {
            "coverage": {"score": 90, "rationale": "covers everything"},
            "groundedness": {"score": 80, "rationale": "ok"},
            "source_authority": {"score": 70, "rationale": "ok"},
            "synthesis_readiness": {"score": 60, "rationale": "ok"},
        }
    )

    result = judge_pack(tmp_path, {"prompt": "p"}, client=lambda _p: response)

    assert result["skipped"] is False
    # Weighted: 90*.35 + 80*.25 + 70*.20 + 60*.20 = 78.5 -> 78
    assert result["score"] == 78
    assert result["doc_sample_count"] == 3
    assert result["dimensions"]["coverage"]["score"] == 90


def test_unknown_dimensions_are_excluded_from_blend(tmp_path: Path) -> None:
    _write_pack(tmp_path, [{"url": "https://x", "title": "t", "content": "c"}])
    response = json.dumps(
        {
            "coverage": {"score": 90, "rationale": "ok"},
            "groundedness": {"score": "Unknown", "rationale": "not enough evidence"},
            "source_authority": {"score": 80, "rationale": "ok"},
            "synthesis_readiness": {"score": "Unknown", "rationale": "n/a"},
        }
    )

    result = judge_pack(tmp_path, {"prompt": "p"}, client=lambda _p: response)

    assert result["dimensions"]["groundedness"]["score"] is None
    # Only coverage(.35) + source_authority(.20) contribute; renormalized
    # (90*.35 + 80*.20) / (.35 + .20) = 86.36 -> 86
    assert result["score"] == 86


def test_non_json_response_is_skipped(tmp_path: Path) -> None:
    _write_pack(tmp_path, [{"url": "https://x", "title": "t", "content": "c"}])
    result = judge_pack(tmp_path, {"prompt": "p"}, client=lambda _p: "I cannot grade this")
    assert result["skipped"] is True
    assert "non-JSON" in result["skip_reason"]


def test_response_clamped_to_0_100(tmp_path: Path) -> None:
    _write_pack(tmp_path, [{"url": "https://x", "title": "t", "content": "c"}])
    response = json.dumps(
        {
            "coverage": {"score": 150, "rationale": "ok"},
            "groundedness": {"score": -10, "rationale": "ok"},
            "source_authority": {"score": 50, "rationale": "ok"},
            "synthesis_readiness": {"score": 50, "rationale": "ok"},
        }
    )

    result = judge_pack(tmp_path, {"prompt": "p"}, client=lambda _p: response)

    assert result["dimensions"]["coverage"]["score"] == 100
    assert result["dimensions"]["groundedness"]["score"] == 0


def test_parse_judgment_finds_json_within_prose() -> None:
    response = 'Here is my judgment:\n\n{"coverage": {"score": 80}}\n\nLet me know.'
    parsed = _parse_judgment(response)
    assert parsed == {"coverage": {"score": 80}}


@pytest.mark.parametrize("bad", ["no braces", "{not json}", "", "{}{"])
def test_parse_judgment_rejects_malformed(bad: str) -> None:
    assert _parse_judgment(bad) is None or _parse_judgment(bad) == {}

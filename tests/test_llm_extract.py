"""Tests for opt-in BYOK LLM structured extraction (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docpull.context_packs.common import ContextPackError
from docpull.context_packs.schema_extract import extract_schema
from docpull.free_core import run_extract_cli
from tests.pack_fixtures import write_context_pack

pytestmark = pytest.mark.internal_legacy


class _FakeClient:
    """Records calls and returns queued responses instead of hitting the API."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.model = "fake-model"
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        self.calls.append((system, user))
        return self._responses.pop(0)


def _schema(tmp_path: Path) -> Path:
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["title"],
                "additionalProperties": False,
                "properties": {"title": {"type": "string"}},
            }
        ),
        encoding="utf-8",
    )
    return schema


def test_llm_mode_happy_path(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    client = _FakeClient(['{"title": "Parallel Search API"}'])

    payload = extract_schema(
        pack,
        schema_path=_schema(tmp_path),
        output_dir=tmp_path / "out",
        mode="llm",
        budget=1.0,
        llm_client=client,
    )

    assert payload["extraction_mode"] == "llm"
    assert payload["provider"] == "byok-anthropic"
    assert payload["data"] == {"title": "Parallel Search API"}
    assert payload["validation"]["valid"] is True
    assert payload["llm"]["model"] == "fake-model"
    assert payload["llm"]["estimated_cost_usd"] == 0.05
    assert payload["llm"]["validator"] == "parity.validate_structured_output"
    assert len(client.calls) == 1


def test_llm_mode_parses_json_in_prose(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    fenced = 'Here is the result:\n```json\n{"title": "Parallel Search API"}\n```\nDone.'
    client = _FakeClient([fenced])

    payload = extract_schema(
        pack,
        schema_path=_schema(tmp_path),
        output_dir=tmp_path / "out",
        mode="llm",
        budget=1.0,
        llm_client=client,
    )

    assert payload["data"] == {"title": "Parallel Search API"}


def test_llm_mode_retries_once_with_validator_feedback(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    # First response omits the required field, second one fixes it.
    client = _FakeClient(['{"wrong": 1}', '{"title": "Fixed"}'])

    payload = extract_schema(
        pack,
        schema_path=_schema(tmp_path),
        output_dir=tmp_path / "out",
        mode="llm",
        budget=1.0,
        llm_client=client,
    )

    assert payload["data"] == {"title": "Fixed"}
    assert payload["llm"]["attempts"] == 2
    assert len(client.calls) == 2
    # The retry prompt carries the validation errors back to the model.
    assert "did not validate" in client.calls[1][1]


def test_llm_mode_double_failure_returns_structured_error(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    client = _FakeClient(["not json at all", "still not json"])

    payload = extract_schema(
        pack,
        schema_path=_schema(tmp_path),
        output_dir=tmp_path / "out",
        mode="llm",
        budget=1.0,
        llm_client=client,
    )

    assert payload["status"] == "failed"
    assert payload["data"] is None
    assert payload["errors"]
    assert len(client.calls) == 2


def test_llm_mode_without_key_or_client_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pack = tmp_path / "pack"
    write_context_pack(pack)

    payload = extract_schema(
        pack,
        schema_path=_schema(tmp_path),
        output_dir=tmp_path / "out",
        mode="llm",
        budget=1.0,
    )

    assert payload["status"] == "failed"
    assert "ANTHROPIC_API_KEY" in payload["errors"][0]


def test_llm_mode_blocked_by_zero_budget_before_any_call(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)

    class _NeverCalled:
        model = "unused"

        def complete(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("client must not be called under a zero budget")

    payload = extract_schema(
        pack,
        schema_path=_schema(tmp_path),
        output_dir=tmp_path / "out",
        mode="llm",
        budget=0,
        llm_client=_NeverCalled(),
    )

    assert payload["status"] == "blocked_by_budget"
    assert payload["blocked_by_budget"] is True
    assert payload["data"] is None


def test_deterministic_mode_unchanged_and_makes_no_call(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)

    payload = extract_schema(
        pack,
        schema_path=_schema(tmp_path),
        output_dir=tmp_path / "out",
    )

    assert payload["extraction_mode"] == "deterministic"
    assert payload["provider"] == "local"
    assert "llm" not in payload


def test_unknown_mode_rejected(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)

    with pytest.raises(ContextPackError):
        extract_schema(
            pack,
            schema_path=_schema(tmp_path),
            output_dir=tmp_path / "out",
            mode="bogus",
        )


def test_extract_cli_accepts_mode_and_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pack = tmp_path / "pack"
    write_context_pack(pack)
    schema = _schema(tmp_path)

    # No key, budget default 0 -> command completes (prints structured error) without raising.
    exit_code = run_extract_cli(
        [
            str(pack),
            "--schema",
            str(schema),
            "--mode",
            "llm",
            "--model",
            "claude-test",
            "--output-dir",
            str(tmp_path / "out"),
            "--json",
        ]
    )
    assert exit_code == 0

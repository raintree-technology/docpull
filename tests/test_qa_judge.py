"""Tests for the task-grounded QA judge and its FreshDocs Bench integration.

No test calls the real Anthropic API: ``judge_qa_answers`` and
``freshdocs_bench`` both accept an injected client callable, and the no-key
paths must skip or fall back cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docpull.eval_grade import EvalGradeError, freshdocs_bench, generate_eval_pack
from docpull.judge import (
    JUDGE_API_KEY_ENV,
    _JudgeTransportError,
    judge_qa_answers,
)

CORRECT = json.dumps({"correct": True, "rationale": "matches the expected claim"})
INCORRECT = json.dumps({"correct": False, "rationale": "contradicts the expected claim"})


class _RecordingClient:
    """Fake judge client that records prompts and replays canned responses."""

    def __init__(self, responses: list[str]) -> None:
        self.prompts: list[str] = []
        self._responses = list(responses)

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _task(task_id: str, question: str, url: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "input": question,
        "citation_requirements": [{"source_url": url, "citation_id": f"S-{task_id}"}],
    }


def _answer(claim: str, fail_terms: list[str] | None = None) -> dict[str, Any]:
    return {"expected_claims": [{"claim": claim}], "fail_if_contains": fail_terms or []}


def test_qa_judge_grades_correct_and_incorrect_verdicts() -> None:
    tasks = [
        _task("t1", "What does the API return?", "https://docs.example.com/api"),
        _task("t2", "What auth is required?", "https://docs.example.com/auth"),
    ]
    predictions = {
        "t1": {"answer": "It returns cited JSON results."},
        "t2": {"answer": "No auth is needed at all."},
    }
    answers = {
        "t1": _answer("The API returns cited JSON results."),
        "t2": _answer("OAuth bearer tokens are required."),
    }
    client = _RecordingClient([CORRECT, INCORRECT])

    result = judge_qa_answers(tasks, predictions, answers, client=client)

    assert result.skipped is False
    assert [v["verdict"] for v in result.verdicts] == ["correct", "incorrect"]
    assert result.verdicts[0]["correct"] is True
    assert result.verdicts[1]["correct"] is False
    assert result.verdicts[0]["rationale"] == "matches the expected claim"
    assert result.graded_count == 2
    assert result.correct_count == 1
    assert result.ungraded_count == 0
    assert result.accuracy == 0.5
    assert len(client.prompts) == 2


def test_qa_judge_prompt_contains_only_task_fields() -> None:
    task = _task("t1", "What does the API return?", "https://docs.example.com/api")
    answers = {"t1": _answer("The API returns cited JSON results.", ["stale legacy claim"])}
    predictions = {"t1": {"answer": "Cited JSON comes back."}}
    client = _RecordingClient([CORRECT])

    judge_qa_answers([task], predictions, answers, client=client)

    prompt = client.prompts[0]
    assert "What does the API return?" in prompt
    assert "The API returns cited JSON results." in prompt
    assert "https://docs.example.com/api" in prompt
    assert "S-t1" in prompt
    assert "stale legacy claim" in prompt
    assert "Cited JSON comes back." in prompt


def test_qa_judge_marks_malformed_output_ungraded() -> None:
    """Documented rule: unparseable verdicts are 'ungraded' and excluded from accuracy."""
    task = _task("t1", "Q?", "https://docs.example.com/a")
    result = judge_qa_answers(
        [task],
        {"t1": {"answer": "some answer"}},
        {"t1": _answer("claim")},
        client=lambda _p: "I cannot grade this",
    )

    assert result.skipped is False
    assert result.verdicts[0]["verdict"] == "ungraded"
    assert result.verdicts[0]["correct"] is None
    assert result.graded_count == 0
    assert result.ungraded_count == 1
    assert result.accuracy is None


def test_qa_judge_transport_error_marks_task_ungraded() -> None:
    def _raise(_prompt: str) -> str:
        raise _JudgeTransportError("HTTP 500")

    task = _task("t1", "Q?", "https://docs.example.com/a")
    result = judge_qa_answers([task], {"t1": {"answer": "a"}}, {"t1": _answer("c")}, client=_raise)

    assert result.verdicts[0]["verdict"] == "ungraded"
    assert "transport error" in str(result.verdicts[0]["rationale"])
    assert result.accuracy is None


def test_qa_judge_missing_prediction_is_ungraded() -> None:
    task = _task("t1", "Q?", "https://docs.example.com/a")
    client = _RecordingClient([CORRECT])

    result = judge_qa_answers([task], {}, {"t1": _answer("c")}, client=client)

    assert result.verdicts[0]["verdict"] == "ungraded"
    assert result.verdicts[0]["rationale"] == "no prediction provided for this task"
    assert client.prompts == []


def test_qa_judge_skips_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(JUDGE_API_KEY_ENV, raising=False)
    task = _task("t1", "Q?", "https://docs.example.com/a")

    result = judge_qa_answers([task], {"t1": {"answer": "a"}}, {"t1": _answer("c")})

    assert result.skipped is True
    assert result.skip_reason == f"{JUDGE_API_KEY_ENV} not set"
    assert result.verdicts == []
    assert result.accuracy is None


# --- freshdocs_bench grader integration -----------------------------------


def _write_bench_pack(pack_dir: Path, records: list[dict[str, Any]]) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = pack_dir / "sources"
    sources_dir.mkdir(exist_ok=True)
    sources = []
    for index, record in enumerate(records, start=1):
        source_path = sources_dir / f"{index:02d}.md"
        source_path.write_text(str(record["content"]), encoding="utf-8")
        sources.append(
            {
                "index": index,
                "url": record["url"],
                "title": record["title"],
                "path": f"sources/{index:02d}.md",
            }
        )
    (pack_dir / "documents.ndjson").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    (pack_dir / "local.pack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "local",
                "workflow": "context-pack",
                "request_options": {"source_policy": {"include_domains": ["docs.example.com"]}},
                "record_count": len(records),
                "sources": sources,
                "artifacts": {"documents_ndjson": "documents.ndjson"},
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "sources.md").write_text("# Sources\n", encoding="utf-8")


def _bench_record(index: int) -> dict[str, Any]:
    return {
        "document_id": f"doc_{index}",
        "url": f"https://docs.example.com/page-{index}",
        "title": f"Page {index}",
        "content": (
            f"Page {index} of the Example API returns current cited JSON results for agents. "
            "Clients should always rely on the documented response schema."
        ),
        "content_hash": f"hash{index}",
        "source_type": "test",
    }


def _make_eval_pack(tmp_path: Path, count: int) -> tuple[Path, list[dict[str, Any]]]:
    pack = tmp_path / "pack"
    _write_bench_pack(pack, [_bench_record(index) for index in range(count)])
    generate_eval_pack(pack, types=["current-context-qa"], limit=count)
    tasks = [
        json.loads(line)
        for line in (pack / "evals" / "tasks.public.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return pack, tasks


def _write_predictions(path: Path, items: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")
    return path


def test_freshdocs_llm_grader_uses_judge_verdicts(tmp_path: Path) -> None:
    pack, tasks = _make_eval_pack(tmp_path, 1)
    # No citation in the answer: the deterministic grader fails this task.
    predictions = _write_predictions(
        tmp_path / "predictions.jsonl",
        [{"id": tasks[0]["id"], "answer": "The endpoint returns current cited JSON results."}],
    )
    client = _RecordingClient([CORRECT])

    report = freshdocs_bench(pack, predictions_path=predictions, grader="llm", judge_client=client)

    summary = report["summary"]
    assert summary["grader"] == "llm"
    assert summary["pass_rate_deterministic"] == 0.0
    assert summary["pass_rate"] == 1.0
    assert summary["pass_rate_final"] == 1.0
    assert summary["llm_grader"]["status"] == "completed"
    assert summary["llm_grader"]["accuracy"] == 1.0
    verdict = report["grader_verdicts"][tasks[0]["id"]]
    assert verdict["verdict"] == "correct"
    result = report["results"][0]
    assert result["passed"] is True
    assert result["passed_deterministic"] is False
    assert len(client.prompts) == 1


def test_freshdocs_llm_grader_falls_back_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(JUDGE_API_KEY_ENV, raising=False)
    pack, tasks = _make_eval_pack(tmp_path, 1)
    predictions = _write_predictions(
        tmp_path / "predictions.jsonl",
        [{"id": tasks[0]["id"], "answer": "No citation in this answer."}],
    )

    report = freshdocs_bench(pack, predictions_path=predictions, grader="llm")

    summary = report["summary"]
    assert summary["llm_grader"]["status"] == "skipped_no_api_key"
    assert summary["pass_rate"] == 0.0
    assert summary["pass_rate_deterministic"] == 0.0
    assert summary["pass_rate_final"] == 0.0
    assert report["grader_verdicts"] == {}
    assert "passed_deterministic" not in report["results"][0]


def test_freshdocs_hybrid_rescues_failures_and_reports_both_rates(tmp_path: Path) -> None:
    pack, tasks = _make_eval_pack(tmp_path, 2)
    passing_task, failing_task = tasks[0], tasks[1]
    predictions = _write_predictions(
        tmp_path / "predictions.jsonl",
        [
            {
                "id": passing_task["id"],
                "answer": f"Cited JSON per {passing_task['source_url']}",
            },
            {
                "id": failing_task["id"],
                "answer": "Paraphrased but correct answer without any citation.",
            },
        ],
    )
    client = _RecordingClient([CORRECT])

    report = freshdocs_bench(pack, predictions_path=predictions, grader="hybrid", judge_client=client)

    # Only the deterministic failure is sent to the judge.
    assert len(client.prompts) == 1
    assert failing_task["input"] in client.prompts[0]
    summary = report["summary"]
    assert summary["grader"] == "hybrid"
    assert summary["pass_rate_deterministic"] == 0.5
    assert summary["pass_rate"] == 1.0
    assert summary["pass_rate_final"] == 1.0
    results_by_id = {item["id"]: item for item in report["results"]}
    assert results_by_id[passing_task["id"]]["passed"] is True
    assert results_by_id[passing_task["id"]]["passed_deterministic"] is True
    assert results_by_id[failing_task["id"]]["passed"] is True
    assert results_by_id[failing_task["id"]]["passed_deterministic"] is False
    assert report["grader_verdicts"][failing_task["id"]]["verdict"] == "correct"
    assert passing_task["id"] not in report["grader_verdicts"]


def test_freshdocs_hybrid_ungraded_verdict_keeps_deterministic_result(tmp_path: Path) -> None:
    pack, tasks = _make_eval_pack(tmp_path, 1)
    predictions = _write_predictions(
        tmp_path / "predictions.jsonl",
        [{"id": tasks[0]["id"], "answer": "No citation in this answer."}],
    )
    client = _RecordingClient(["not a json verdict"])

    report = freshdocs_bench(pack, predictions_path=predictions, grader="hybrid", judge_client=client)

    summary = report["summary"]
    assert summary["pass_rate"] == 0.0
    assert summary["llm_grader"]["status"] == "completed"
    assert summary["llm_grader"]["ungraded_count"] == 1
    assert summary["llm_grader"]["accuracy"] is None
    assert report["results"][0]["passed"] is False
    assert report["grader_verdicts"][tasks[0]["id"]]["verdict"] == "ungraded"


def test_freshdocs_default_grader_report_is_unchanged(tmp_path: Path) -> None:
    pack, tasks = _make_eval_pack(tmp_path, 1)
    predictions = _write_predictions(
        tmp_path / "predictions.jsonl",
        [{"id": tasks[0]["id"], "answer": f"Cited per {tasks[0]['source_url']}"}],
    )

    report = freshdocs_bench(pack, predictions_path=predictions)

    summary = report["summary"]
    assert summary["grader"] == "deterministic"
    assert summary["pass_rate"] == 1.0
    assert "llm_grader" not in summary
    assert "pass_rate_deterministic" not in summary
    assert "grader_verdicts" not in report
    assert "passed_deterministic" not in report["results"][0]


def test_freshdocs_rejects_unknown_grader(tmp_path: Path) -> None:
    pack, _tasks = _make_eval_pack(tmp_path, 1)
    with pytest.raises(EvalGradeError, match="Unsupported grader"):
        freshdocs_bench(pack, grader="vibes")

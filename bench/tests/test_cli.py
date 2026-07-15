from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from docpull_bench import cli
from docpull_bench.models import BenchmarkSuite, Lane


def test_context_alias_selects_all_supported_context_contract_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, argparse.Namespace] = {}

    def fake_run(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli, "_run", fake_run)

    assert cli.main(["context", "--repeat", "2", "--max-concurrency", "3"]) == 0
    args = captured["args"]
    suite = BenchmarkSuite.from_yaml(Path("bench/cases/controlled-v2.yaml"))
    selected = {case.id: case.input.lane for case in suite.cases if case.id in args.case_ids}

    assert len(selected) == 130
    assert set(selected.values()) == {
        Lane.PARSE,
        Lane.PACK,
        Lane.LIFECYCLE,
        Lane.RETRIEVAL,
    }
    assert args.repeat == 2
    assert args.max_concurrency == 3
    assert args.network_isolation == "enforced"

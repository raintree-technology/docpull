from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_update_metrics_module():
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / ".github" / "scripts" / "update_metrics.py"
    spec = importlib.util.spec_from_file_location("update_metrics", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_snapshot_rows_formats_all_expected_metrics() -> None:
    update_metrics = _load_update_metrics_module()

    rows = update_metrics.build_snapshot_rows(
        recent={"last_day": 12, "last_week": 3456, "last_month": 78901},
        repo={"stargazers_count": 11, "forks_count": 22, "subscribers_count": 33},
        open_issues=4,
        open_prs=5,
        clones={"count": 66, "uniques": 7},
        views={"count": 88, "uniques": 9},
    )

    assert rows == [
        ["PyPI downloads (last 24h)", "12"],
        ["PyPI downloads (last 7d)", "3,456"],
        ["PyPI downloads (last 30d)", "78,901"],
        ["GitHub stars", "11"],
        ["GitHub forks", "22"],
        ["GitHub watchers", "33"],
        ["Open issues", "4"],
        ["Open PRs", "5"],
        ["Repo clones (last 14d)", "66"],
        ["Unique cloners (last 14d)", "7"],
        ["Repo views (last 14d)", "88"],
        ["Unique visitors (last 14d)", "9"],
    ]


def test_build_path_rows_trims_repo_prefix_and_wraps_code() -> None:
    update_metrics = _load_update_metrics_module()

    rows = update_metrics.build_path_rows(
        [
            {"path": "/raintree-technology/docpull", "count": 10, "uniques": 2},
            {"path": "/raintree-technology/docpull/docs/getting-started", "count": 3, "uniques": 1},
        ]
    )

    assert rows == [
        ["`/`", "10", "2"],
        ["`/docs/getting-started`", "3", "1"],
    ]


def test_append_section_with_table_uses_empty_state_when_no_rows() -> None:
    update_metrics = _load_update_metrics_module()

    lines: list[str] = []
    update_metrics.append_section_with_table(
        lines,
        "## Example",
        ["Col A", "Col B"],
        [],
        empty_message="_Nothing here._",
    )

    assert lines == [
        "## Example",
        "",
        "_Nothing here._",
        "",
    ]


def test_append_table_or_empty_uses_table_when_rows_exist() -> None:
    update_metrics = _load_update_metrics_module()

    lines: list[str] = []
    update_metrics.append_table_or_empty(
        lines,
        ["Col A", "Col B"],
        [["x", "y"]],
        empty_message="_Nothing here._",
    )

    assert lines == [
        "| Col A | Col B |",
        "|---|---|",
        "| x | y |",
        "",
    ]

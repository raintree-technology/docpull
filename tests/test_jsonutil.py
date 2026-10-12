from __future__ import annotations

from pathlib import Path

import pytest

from docpull._jsonutil import read_json, slugify, string_list, write_json


class _FixtureError(Exception):
    pass


def test_read_json_handles_missing_invalid_and_round_trip(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(_FixtureError, match="Missing required file"):
        read_json(missing, error=_FixtureError)
    assert read_json(missing, error=_FixtureError, required=False, default={"a": 1}) == {"a": 1}

    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(_FixtureError, match="Invalid JSON"):
        read_json(bad, error=_FixtureError, required=False)

    target = tmp_path / "nested" / "out.json"
    write_json(target, {"b": "é", "a": 1}, sort_keys=True)
    assert target.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": "é"\n}\n'
    assert read_json(target, error=_FixtureError) == {"a": 1, "b": "é"}


def test_string_list_and_slugify() -> None:
    assert string_list([" a ", "", 3, "  "]) == ["a", "3"]
    assert string_list("a") == []
    assert slugify("Hello, World!", fallback="item") == "hello-world"
    assert slugify("!!!", fallback="item") == "item"

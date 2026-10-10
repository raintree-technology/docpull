"""Keep unsupported interpreters away from vulnerable legacy parser releases."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull import document_parse


@pytest.mark.parametrize("version", [(3, 10), (3, 14), (3, 15)])
def test_unstructured_rejects_unsupported_python_before_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: tuple[int, int]
) -> None:
    monkeypatch.setattr(document_parse, "sys", SimpleNamespace(version_info=version))
    monkeypatch.setattr(
        document_parse.importlib,
        "import_module",
        lambda name: pytest.fail("Unsupported Unstructured backend must not be imported"),
    )
    with pytest.raises(document_parse.DocumentParseError, match="requires Python 3.11–3.13"):
        document_parse._parse_unstructured(tmp_path / "example.txt")


@pytest.mark.parametrize("version", [(3, 11), (3, 12), (3, 13)])
def test_unstructured_supported_python_passes_local_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: tuple[int, int]
) -> None:
    source = tmp_path / "example.txt"
    source.write_text("Local evidence")
    monkeypatch.setattr(document_parse, "sys", SimpleNamespace(version_info=version))
    calls = []

    def partition(*, filename: str) -> list[str]:
        calls.append(filename)
        return ["Local evidence"]

    monkeypatch.setattr(
        document_parse.importlib, "import_module", lambda name: SimpleNamespace(partition=partition)
    )
    content, metadata = document_parse._parse_unstructured(source)
    assert content == "Local evidence"
    assert metadata["element_count"] == 1
    assert calls == [str(source)]


def test_text_backend_remains_available_on_python_310(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "example.txt"
    source.write_text("Local evidence")
    monkeypatch.setattr(document_parse, "sys", SimpleNamespace(version_info=(3, 10)))
    result = document_parse.parse_one_document(source, backend="text", source_url=source.as_uri(), title=None)
    assert "Local evidence" in result.content

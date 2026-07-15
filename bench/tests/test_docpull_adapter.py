from __future__ import annotations

from pathlib import Path

from docpull_bench.adapters import docpull as docpull_module


def test_docpull_adapter_preserves_virtualenv_launcher_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "base-python"
    target.touch()
    launcher = tmp_path / "venv" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(target)
    captured: list[str] = []

    def installed_version(python_executable: str) -> str:
        captured.append(python_executable)
        return "6.1.0"

    monkeypatch.setattr(docpull_module, "_installed_docpull_version", installed_version)

    adapter = docpull_module.DocPullAdapter(python_executable=launcher)

    assert adapter.python_executable == str(launcher.absolute())
    assert captured == [str(launcher.absolute())]
    assert Path(adapter.python_executable).is_symlink()

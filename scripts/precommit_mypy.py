"""Run mypy from the project interpreter for pre-commit."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_python(repo_root: Path) -> str:
    if os.name == "nt":
        venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    python = _project_python(repo_root)
    # Selected Python executable with static mypy argv; no shell.
    os.execv(  # nosec B606
        python,
        [python, "-m", "mypy", "src"],
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

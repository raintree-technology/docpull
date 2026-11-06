"""Tests for the release claim audit gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_claim_audit_rejects_unsupported_absolutes(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("DocPull guarantees complete browser coverage.\n", encoding="utf-8")
    (repo / "docs" / "release-claims.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "area": "Example",
                        "claim": "Example claim",
                        "references": [
                            {"type": "code", "path": "README.md"},
                            {"type": "test", "path": "README.md"},
                            {"type": "doc", "path": "README.md"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "claim_audit.py"),
            "--repo",
            str(repo),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert {issue["code"] for issue in payload["issues"]} == {"unsupported_absolute"}

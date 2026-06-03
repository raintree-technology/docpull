"""Repository policy checks for CI and release workflows."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USE_RE = re.compile(r"uses:\s*([^@\s]+)@([^\s]+)")


def test_github_actions_are_pinned_to_full_commit_shas() -> None:
    offenders: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = USE_RE.search(line)
            if match and not FULL_SHA_RE.fullmatch(match.group(2)):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {match.group(0)}")

    assert offenders == []


def test_workflows_do_not_use_latest_container_tags() -> None:
    offenders: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if ":latest" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert offenders == []


def test_publish_workflow_is_tag_only() -> None:
    publish = (WORKFLOW_DIR / "publish.yml").read_text()
    assert "workflow_dispatch" not in publish
    assert '"v*.*.*"' in publish

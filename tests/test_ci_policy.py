"""Repository policy checks for CI and release workflows."""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

from docpull.mcp.sources import default_docs_dir

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


def test_publish_workflow_accepts_only_tags_or_guarded_manual_dispatch() -> None:
    publish = (WORKFLOW_DIR / "publish.yml").read_text()
    assert '"v*.*.*"' in publish
    assert "workflow_dispatch:" in publish
    assert "version:" in publish
    assert "required: true" in publish
    assert 'elif [ "${GITHUB_EVENT_NAME}" = "workflow_dispatch" ]; then' in publish
    assert 'if [ "${GITHUB_REF_NAME}" != "main" ]; then' in publish
    assert 'if [ "$REQUESTED_VERSION" != "$PROJECT_VERSION" ]; then' in publish


def test_plugin_readme_cache_path_matches_mcp_default() -> None:
    readme = (REPO_ROOT / "plugin" / "README.md").read_text(encoding="utf-8")

    with mock.patch.dict("os.environ", {}, clear=True):
        default_path = default_docs_dir()

    assert default_path.parts[-2:] == ("docpull-mcp", "docs")
    assert "$XDG_DATA_HOME/docpull-mcp/docs/" in readme
    assert "~/.local/share/docpull-mcp/docs/" in readme
    assert "4.4.0 or newer" in readme

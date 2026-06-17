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
PYTHON_CLASSIFIER_RE = re.compile(r'"Programming Language :: Python :: (3\.\d+)"')
CI_MATRIX_RE = re.compile(r"python-version:\s*\[(?P<versions>[^\]]+)\]")


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


def test_ci_matrix_covers_advertised_python_minors() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    ci = (WORKFLOW_DIR / "ci.yml").read_text()

    advertised = set(PYTHON_CLASSIFIER_RE.findall(pyproject))
    matrix_match = CI_MATRIX_RE.search(ci)
    assert matrix_match is not None
    tested = set(re.findall(r'"(3\.\d+)"', matrix_match.group("versions")))

    assert tested == advertised


def test_ci_builds_checks_and_smoke_installs_distribution() -> None:
    ci = (WORKFLOW_DIR / "ci.yml").read_text()

    assert "\n  package:\n" in ci
    assert "python -m pip install -r requirements-release.txt" in ci
    assert "python -m build --no-isolation" in ci
    assert "python -m twine check dist/*" in ci
    assert "python -m venv .pkg-smoke" in ci
    assert ".pkg-smoke/bin/python -m pip install dist/*.whl" in ci
    assert ".pkg-smoke/bin/docpull --version" in ci


def test_publish_workflow_smoke_installs_distribution_before_upload() -> None:
    publish = (WORKFLOW_DIR / "publish.yml").read_text()

    assert "python -m venv .release-smoke" in publish
    assert ".release-smoke/bin/python -m pip install dist/*.whl" in publish
    assert ".release-smoke/bin/docpull --version" in publish


def test_security_and_publish_bandit_scan_scripts() -> None:
    security = (WORKFLOW_DIR / "security.yml").read_text()
    publish = (WORKFLOW_DIR / "publish.yml").read_text()

    assert "python -m bandit -q -c pyproject.toml -r src scripts" in security
    assert "python -m bandit -q -c pyproject.toml -r src scripts" in publish


def test_workflows_use_module_entrypoints_for_python_tooling() -> None:
    raw_tool_prefixes = (
        "ruff ",
        "mypy ",
        "pytest ",
        "pip-audit",
        "bandit ",
        "pre-commit ",
    )
    env_prefix_re = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)+")
    offenders: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            command = stripped.removeprefix("run: ")
            command = env_prefix_re.sub("", command)
            if command.startswith(raw_tool_prefixes):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {stripped}")

    assert offenders == []


def test_pre_commit_mypy_uses_project_interpreter_wrapper() -> None:
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()

    assert "entry: python3 scripts/precommit_mypy.py" in config
    assert "entry: mypy src" not in config


def test_plugin_readme_cache_path_matches_mcp_default() -> None:
    readme = (REPO_ROOT / "plugin" / "README.md").read_text(encoding="utf-8")

    with mock.patch.dict("os.environ", {}, clear=True):
        default_path = default_docs_dir()

    assert default_path.parts[-2:] == ("docpull-mcp", "docs")
    assert "$XDG_DATA_HOME/docpull-mcp/docs/" in readme
    assert "~/.local/share/docpull-mcp/docs/" in readme
    assert "4.4.0 or newer" in readme

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


def test_publish_workflow_requires_main_branch_provenance() -> None:
    publish = (WORKFLOW_DIR / "publish.yml").read_text()
    assert "fetch-depth: 0" in publish
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" "origin/main"' in publish


def test_ci_matrix_covers_declared_supported_python_versions() -> None:
    ci = (WORKFLOW_DIR / "ci.yml").read_text()
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]' in ci


def test_python_security_audits_shipped_optional_dependencies() -> None:
    security = (WORKFLOW_DIR / "security.yml").read_text()
    assert "dependency-groups: all,dev" in security
    assert "make python-security" in security


def test_benchmark_workflow_watches_full_python_source_tree() -> None:
    benchmark = (WORKFLOW_DIR / "benchmark.yml").read_text()
    assert '- "src/docpull/**"' in benchmark


def test_python_workflows_use_shared_setup_action() -> None:
    for workflow_name in ["benchmark.yml", "ci.yml", "publish.yml", "security.yml"]:
        workflow = (WORKFLOW_DIR / workflow_name).read_text()
        assert "uses: ./.github/actions/setup-python-docpull" in workflow


def test_workflows_delegate_python_gate_commands_to_makefile() -> None:
    ci = (WORKFLOW_DIR / "ci.yml").read_text()
    publish = (WORKFLOW_DIR / "publish.yml").read_text()
    security = (WORKFLOW_DIR / "security.yml").read_text()
    benchmark = (WORKFLOW_DIR / "benchmark.yml").read_text()

    assert "make test-cov" in ci
    assert "make lint-check" in ci
    assert "make pre-commit-check" in ci
    assert "make typecheck" in ci
    assert "make release-gates" in publish
    assert "make python-security" in security
    assert "make benchmark-10k" in benchmark
    assert "set -o pipefail" in benchmark


def test_web_security_job_uses_declared_node_major() -> None:
    security = (WORKFLOW_DIR / "security.yml").read_text()
    assert 'node-version: "24"' in security

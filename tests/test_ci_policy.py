"""Repository policy checks for CI and release workflows."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
import sys
from pathlib import Path
from unittest import mock

import yaml

from docpull.mcp.sources import default_docs_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USE_RE = re.compile(r"uses:\s*([^@\s]+)@([^\s]+)")
PYTHON_CLASSIFIER_RE = re.compile(r'"Programming Language :: Python :: (3\.\d+)"')
CI_MATRIX_RE = re.compile(r"python-version:\s*\[(?P<versions>[^\]]+)\]")
SECTION_RE = re.compile(r"^\[[^\]]+]")
PROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"(?:\s*#.*)?$')
ACTION_PIN_EXCEPTIONS = {
    # The PyPI trusted-publishing action failed workflow startup when pinned to
    # the resolved commit SHA in this repository; release/v1 is the upstream
    # supported stable entrypoint for the OIDC publish flow.
    ("pypa/gh-action-pypi-publish", "release/v1"),
}
APPROVED_ACTION_SHAS = {
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
}


def project_version() -> str:
    in_project = False
    for line in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and SECTION_RE.match(stripped):
            break
        if in_project:
            match = PROJECT_VERSION_RE.match(stripped)
            if match:
                return match.group(1)
    raise AssertionError("Could not find [project].version in pyproject.toml")


def test_github_actions_are_pinned_to_full_commit_shas() -> None:
    offenders: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = USE_RE.search(line)
            if (
                match
                and (match.group(1), match.group(2)) not in ACTION_PIN_EXCEPTIONS
                and not FULL_SHA_RE.fullmatch(match.group(2))
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {match.group(0)}")

    assert offenders == []


def test_critical_github_actions_use_approved_resolvable_shas() -> None:
    offenders: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = USE_RE.search(line)
            if match is None or match.group(1) not in APPROVED_ACTION_SHAS:
                continue
            if match.group(2) != APPROVED_ACTION_SHAS[match.group(1)]:
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
    tested_versions = re.findall(r'"(3\.\d+(?:\.\d+)?)"', matrix_match.group("versions"))
    tested = {".".join(version.split(".")[:2]) for version in tested_versions}

    assert tested == advertised
    assert "MISE_PYTHON_VERSION: ${{ matrix.python-version }}" in ci


def test_production_workflows_keep_automatic_triggers() -> None:
    ci = (WORKFLOW_DIR / "ci.yml").read_text()
    codeql = (WORKFLOW_DIR / "codeql.yml").read_text()
    security = (WORKFLOW_DIR / "security.yml").read_text()
    benchmark = (WORKFLOW_DIR / "benchmark.yml").read_text()
    live_typed = (WORKFLOW_DIR / "live-typed-packs.yml").read_text()
    live_web = (WORKFLOW_DIR / "live-web-smoke.yml").read_text()
    metrics = (WORKFLOW_DIR / "metrics.yml").read_text()

    for workflow in (ci, codeql, security):
        assert "pull_request:" in workflow
        assert "push:" in workflow
        assert "- main" in workflow
    assert 'cron: "38 6 * * 1"' in codeql
    assert 'cron: "17 4 * * 3"' in benchmark
    assert 'cron: "17 11 * * *"' in live_typed
    assert 'cron: "43 10 * * *"' in live_web
    assert 'cron: "12 9 * * 1"' in metrics


def test_uv_lock_is_committed_and_used() -> None:
    assert (REPO_ROOT / "uv.lock").is_file()
    assert "uv.lock" not in (REPO_ROOT / ".gitignore").read_text().splitlines()


def test_ci_builds_checks_and_smoke_installs_distribution() -> None:
    ci = (WORKFLOW_DIR / "ci.yml").read_text()

    assert "\n  package:\n" in ci
    assert "uv run --locked python scripts/sync_release_metadata.py --check" in ci
    assert (
        "uv run --locked --with-requirements requirements-release.txt "
        "python scripts/build_release.py --verify-reproducible"
    ) in ci
    assert "python -m build --no-isolation" not in ci
    assert "python -m twine check dist/*" in ci
    assert "uv venv .pkg-smoke" in ci
    assert "uv pip install --python .pkg-smoke/bin/python dist/*.whl" in ci
    assert ".pkg-smoke/bin/docpull --version" in ci


def test_publish_workflow_builds_artifact_before_locked_release_gates() -> None:
    publish = (WORKFLOW_DIR / "publish.yml").read_text()
    build_section, gate_and_publish = publish.split("\n  release-gates:\n", 1)
    gate_section, publish_section = gate_and_publish.split("\n  publish:\n", 1)

    assert (
        "uv run --locked --with-requirements requirements-release.txt "
        "python scripts/build_release.py --verify-reproducible"
    ) in build_section
    assert "python -m build --no-isolation" not in build_section
    assert "actions/upload-artifact" in build_section
    assert "if-no-files-found: error" in build_section
    assert "retention-days: 7" in build_section
    assert 'pip install --no-build-isolation -e ".[all,dev]"' not in build_section

    assert "uv sync --locked --all-extras" in gate_section
    assert (
        "uv run --locked --all-extras --with-requirements requirements-release.txt "
        "python scripts/release_a_plus_check.py"
    ) in gate_section.replace("\\\n            ", "")
    assert "uv run --locked python scripts/sync_release_metadata.py --check" in publish
    assert "actions/download-artifact" in gate_section
    assert "uv venv .release-smoke" in publish
    assert "uv pip install --python .release-smoke/bin/python dist/*.whl" in publish
    assert ".release-smoke/bin/docpull --version" in publish
    assert "needs: [build, release-gates]" in publish_section


def test_publish_workflow_creates_github_release_after_pypi_publish() -> None:
    publish = (WORKFLOW_DIR / "publish.yml").read_text()
    _, publish_section = publish.split("\n  publish:\n", 1)

    assert "contents: write" in publish_section
    assert "id-token: write" in publish_section
    assert "pypa/gh-action-pypi-publish@" in publish_section
    assert "# release/v1" in publish_section
    assert "name: Create GitHub release" in publish_section
    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in publish_section
    assert "GH_TOKEN: ${{ github.token }}" in publish_section
    assert 'NOTES_FILE="docs/release-post-v${MINOR_VERSION}.md"' in publish_section
    assert (
        'gh release edit "$TAG" --title "$RELEASE_TITLE" --latest --notes-file "$NOTES_FILE"'
        in publish_section
    )
    assert 'gh release edit "$TAG" --title "$RELEASE_TITLE" --latest' in publish_section
    assert (
        'gh release create "$TAG" --title "$RELEASE_TITLE" --verify-tag --latest --notes-file "$NOTES_FILE"'
        in publish_section
    )
    assert (
        'gh release create "$TAG" --title "$RELEASE_TITLE" --verify-tag --latest --generate-notes'
        in publish_section
    )


def test_security_and_publish_bandit_scan_scripts() -> None:
    security = (WORKFLOW_DIR / "security.yml").read_text()
    publish = (WORKFLOW_DIR / "publish.yml").read_text()

    assert "python -m bandit -q -c pyproject.toml -r src scripts" in security
    assert "python -m bandit -q -c pyproject.toml -r src scripts" in publish


def test_security_workflow_uses_locked_dependencies_without_a_web_app_job() -> None:
    security = (WORKFLOW_DIR / "security.yml").read_text()
    release_requirements = (REPO_ROOT / "requirements-release.txt").read_text()

    assert "uv sync --locked --extra dev" in security
    assert "setuptools==83.0.0" in release_requirements
    assert "working-directory: web" not in security
    assert "web-security:" not in security


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


def test_pre_commit_mutators_preserve_hashed_benchmark_results() -> None:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())
    hooks = {hook["id"]: hook for repository in config["repos"] for hook in repository["hooks"]}

    for hook_id in ("trailing-whitespace", "end-of-file-fixer", "mixed-line-ending"):
        assert hooks[hook_id]["exclude"] == r"^bench/results/"


def test_local_make_gates_include_generated_metadata_check() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "lint: metadata-check" in makefile
    assert "test-all-local: metadata-check" in makefile
    assert "format: license-year metadata-sync" in makefile
    assert "$(PYTHON) -m ruff check ." in makefile
    assert "$(PYTHON) -m ruff format ." in makefile


def test_release_helper_checks_generated_metadata_on_current_release_ref() -> None:
    release = (REPO_ROOT / "scripts" / "release.py").read_text(encoding="utf-8")

    assert 'run(sys.executable, "scripts/sync_release_metadata.py", "--check")' in release
    assert 'ensure_head_matches("origin/main", "release publish")' in release
    assert 'ensure_head_matches("origin/main", "release dispatch")' in release


def test_plugin_readme_cache_path_matches_mcp_default() -> None:
    readme = (REPO_ROOT / "plugin" / "README.md").read_text(encoding="utf-8")

    with mock.patch.dict("os.environ", {}, clear=True):
        default_path = default_docs_dir()

    assert default_path.parts[-2:] == ("docpull-mcp", "docs")
    assert "$XDG_DATA_HOME/docpull-mcp/docs/" in readme
    assert "~/.local/share/docpull-mcp/docs/" in readme
    assert f"{project_version()} or newer" in readme


def test_plugin_manifest_versions_match_project_version() -> None:
    for manifest_path in (
        REPO_ROOT / "plugin" / ".codex-plugin" / "plugin.json",
        REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json",
    ):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["version"] == project_version()


def test_mcp_registry_manifest_versions_match_project_version() -> None:
    manifest = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))

    assert manifest["version"] == project_version()
    assert len(manifest["packages"]) == 1
    assert manifest["packages"][0]["identifier"] == "docpull"
    assert manifest["packages"][0]["version"] == project_version()


def test_generated_release_metadata_is_synchronized() -> None:
    proc = subprocess.run(  # nosec B603
        [sys.executable, "scripts/sync_release_metadata.py", "--check"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert proc.returncode == 0, proc.stdout

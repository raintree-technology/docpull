"""Consistency checks for the Claude plugin bundle."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback for test envs
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
AUTHORING_PLUGIN_DIR = REPO_ROOT / "plugin"
BUNDLE_ROOT = REPO_ROOT / ".claude-plugin"
BUNDLE_PLUGIN_DIR = BUNDLE_ROOT / "plugin"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_claude_plugin.py"
SYNC_AGENT_HOSTS_SCRIPT = REPO_ROOT / "scripts" / "sync_agent_host_configs.py"


def setup_module() -> None:
    subprocess.run([sys.executable, str(SYNC_SCRIPT)], check=True)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def test_self_contained_bundle_includes_plugin_payload() -> None:
    required_files = [
        BUNDLE_ROOT / "marketplace.json",
        BUNDLE_PLUGIN_DIR / ".claude-plugin" / "plugin.json",
        BUNDLE_PLUGIN_DIR / ".mcp.json",
        BUNDLE_PLUGIN_DIR / "README.md",
        BUNDLE_PLUGIN_DIR / "skills" / "docpull-research" / "SKILL.md",
    ]

    missing = [path.relative_to(REPO_ROOT).as_posix() for path in required_files if not path.exists()]

    assert missing == []


def test_bundle_payload_matches_authoring_plugin_files() -> None:
    relative_files = [
        Path(".mcp.json"),
        Path("README.md"),
        Path("skills/docpull-research/SKILL.md"),
    ]

    for relative_path in relative_files:
        authoring = (AUTHORING_PLUGIN_DIR / relative_path).read_text()
        bundled = (BUNDLE_PLUGIN_DIR / relative_path).read_text()
        assert bundled == authoring, relative_path.as_posix()


def test_plugin_bundle_does_not_ship_host_specific_command_wrappers() -> None:
    assert not (AUTHORING_PLUGIN_DIR / "commands").exists()
    assert not (BUNDLE_PLUGIN_DIR / "commands").exists()


def test_bundle_metadata_matches_authoring_plugin_and_package_version() -> None:
    pyproject = _load_pyproject()
    package_version = pyproject["project"]["version"]
    authoring = _load_json(AUTHORING_PLUGIN_DIR / ".claude-plugin" / "plugin.json")
    bundled = _load_json(BUNDLE_PLUGIN_DIR / ".claude-plugin" / "plugin.json")
    codex = _load_json(AUTHORING_PLUGIN_DIR / ".codex-plugin" / "plugin.json")
    marketplace = _load_json(BUNDLE_ROOT / "marketplace.json")

    assert authoring == bundled
    assert authoring["version"] == package_version
    assert codex["version"] == package_version
    assert codex["skills"] == "./skills/"
    assert marketplace["plugins"][0]["version"] == package_version
    assert marketplace["plugins"][0]["source"] == "./plugin"


def test_plugin_skill_requires_mcp_extra_for_recovery() -> None:
    skill = (AUTHORING_PLUGIN_DIR / "skills" / "docpull-research" / "SKILL.md").read_text()

    assert "pip install 'docpull[mcp]'" in skill
    assert "pip install docpull" not in skill


def test_agent_host_configs_keep_docpull_mcp_aligned() -> None:
    claude_mcp = _load_json(AUTHORING_PLUGIN_DIR / ".mcp.json")
    project_mcp = _load_json(REPO_ROOT / ".mcp.json")
    cursor_mcp = _load_json(REPO_ROOT / ".cursor" / "mcp.json")

    assert project_mcp["mcpServers"]["docpull"] == claude_mcp["mcpServers"]["docpull"]
    assert cursor_mcp["mcpServers"]["docpull"] == {
        "type": "stdio",
        "command": claude_mcp["mcpServers"]["docpull"]["command"],
        "args": claude_mcp["mcpServers"]["docpull"]["args"],
    }

    required_files = [
        REPO_ROOT / "CLAUDE.md",
        REPO_ROOT / ".cursor" / "rules" / "docpull-research.mdc",
        REPO_ROOT / "AGENTS.md",
    ]
    missing = [path.relative_to(REPO_ROOT).as_posix() for path in required_files if not path.exists()]

    assert missing == []


def test_codex_host_sync_script_declares_official_project_paths() -> None:
    result = subprocess.run(
        [sys.executable, str(SYNC_AGENT_HOSTS_SCRIPT), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        ".codex/config.toml",
        ".agents/skills/docpull-research/SKILL.md",
        ".agents/plugins/marketplace.json",
    ]


def test_agent_research_guidance_mentions_skills_cli_docs() -> None:
    required_fragments = [
        "skills.sh",
        "npx skills",
        "skills add",
        "--agent",
        "--skill",
        "--copy",
        "--yes",
    ]
    guidance_files = [
        AUTHORING_PLUGIN_DIR / "skills" / "docpull-research" / "SKILL.md",
        BUNDLE_PLUGIN_DIR / "skills" / "docpull-research" / "SKILL.md",
        REPO_ROOT / ".cursor" / "rules" / "docpull-research.mdc",
        REPO_ROOT / "AGENTS.md",
    ]

    for path in guidance_files:
        text = path.read_text()
        missing = [fragment for fragment in required_fragments if fragment not in text]
        assert missing == [], path.relative_to(REPO_ROOT).as_posix()

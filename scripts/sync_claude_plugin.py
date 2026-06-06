#!/usr/bin/env python3
"""Sync the self-contained Claude plugin bundle from repo sources."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback for dev tooling
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
AUTHORING_PLUGIN_DIR = REPO_ROOT / "plugin"
BUNDLE_ROOT = REPO_ROOT / ".claude-plugin"
BUNDLE_PLUGIN_DIR = BUNDLE_ROOT / "plugin"

PLUGIN_DESCRIPTION = (
    "Pull server-rendered web content from any URL into Claude Code. Indexes sites in seconds with "
    "conditional-GET caching, then exposes them as MCP tools (fetch_url, ensure_docs, list_sources, "
    "list_indexed, grep_docs, read_doc, add_source, remove_source). Local, browser-free, no API keys."
)
MARKETPLACE_DESCRIPTION = (
    "Pull server-rendered web content from any URL into Claude Code. Local, fast, no API keys."
)
PLUGIN_KEYWORDS = [
    "web",
    "crawler",
    "fetch",
    "markdown",
    "rag",
    "mcp",
    "local-first",
]
MARKETPLACE_KEYWORDS = PLUGIN_KEYWORDS[:4] + ["indexing"] + PLUGIN_KEYWORDS[5:]


def load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def sync_bundle_files() -> None:
    if BUNDLE_PLUGIN_DIR.exists():
        shutil.rmtree(BUNDLE_PLUGIN_DIR)

    shutil.copytree(
        AUTHORING_PLUGIN_DIR,
        BUNDLE_PLUGIN_DIR,
        ignore=shutil.ignore_patterns(".claude-plugin"),
    )


def build_plugin_metadata(pyproject: dict) -> dict:
    project = pyproject["project"]
    maintainer = project["maintainers"][0]
    repository_url = project["urls"]["Repository"]

    return {
        "name": project["name"],
        "version": project["version"],
        "description": PLUGIN_DESCRIPTION,
        "author": {
            "name": maintainer["name"],
            "email": maintainer["email"],
            "url": repository_url,
        },
        "homepage": project["urls"]["Homepage"],
        "repository": repository_url,
        "license": project["license"],
        "keywords": PLUGIN_KEYWORDS,
    }


def build_marketplace_metadata(pyproject: dict, plugin_metadata: dict) -> dict:
    author = plugin_metadata["author"]
    return {
        "name": plugin_metadata["name"],
        "owner": {
            "name": author["name"],
            "email": author["email"],
            "url": author["url"],
        },
        "plugins": [
            {
                "name": plugin_metadata["name"],
                "source": "./plugin",
                "description": MARKETPLACE_DESCRIPTION,
                "version": plugin_metadata["version"],
                "author": {
                    "name": author["name"],
                    "email": author["email"],
                },
                "homepage": plugin_metadata["homepage"],
                "repository": plugin_metadata["repository"],
                "license": plugin_metadata["license"],
                "category": "documentation",
                "keywords": MARKETPLACE_KEYWORDS,
            }
        ],
    }


def main() -> None:
    pyproject = load_pyproject()
    sync_bundle_files()

    plugin_metadata = build_plugin_metadata(pyproject)
    write_json(AUTHORING_PLUGIN_DIR / ".claude-plugin" / "plugin.json", plugin_metadata)
    write_json(BUNDLE_PLUGIN_DIR / ".claude-plugin" / "plugin.json", plugin_metadata)
    write_json(BUNDLE_ROOT / "marketplace.json", build_marketplace_metadata(pyproject, plugin_metadata))


if __name__ == "__main__":
    main()

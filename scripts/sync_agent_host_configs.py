#!/usr/bin/env python3
"""Sync project-local agent host config files from repo sources."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SKILL_DIR = REPO_ROOT / "plugin" / "skills" / "docpull-research"
CODEX_CONFIG_PATH = REPO_ROOT / ".codex" / "config.toml"
CODEX_SKILL_DIR = REPO_ROOT / ".agents" / "skills" / "docpull-research"
CODEX_SKILL_PATH = CODEX_SKILL_DIR / "SKILL.md"
CODEX_MARKETPLACE_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"

CODEX_CONFIG = """[mcp_servers.docpull]
command = "docpull"
args = ["mcp"]
"""


def sync(*, dry_run: bool = False) -> list[Path]:
    """Write host config files and return the paths that would be/were touched."""

    paths = [CODEX_CONFIG_PATH, CODEX_SKILL_PATH, CODEX_MARKETPLACE_PATH]
    if dry_run:
        return paths

    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_PATH.write_text(CODEX_CONFIG, encoding="utf-8")

    if CODEX_SKILL_DIR.exists():
        shutil.rmtree(CODEX_SKILL_DIR)
    shutil.copytree(PLUGIN_SKILL_DIR, CODEX_SKILL_DIR)

    CODEX_MARKETPLACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_MARKETPLACE_PATH.write_text(
        json.dumps(
            {
                "name": "docpull-local",
                "plugins": [
                    {
                        "name": "docpull",
                        "source": {"source": "local", "path": "./plugin"},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                        "category": "Documentation",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print files without writing them")
    args = parser.parse_args()

    for path in sync(dry_run=args.dry_run):
        print(path.relative_to(REPO_ROOT).as_posix())


if __name__ == "__main__":
    main()

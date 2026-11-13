"""Resolve default relative state directories against a stable base.

DocPull keeps local state in relative directories such as ``.docpull-cache``
and ``.claude/skills``. Resolved naively, those land wherever the command
happens to run, so running from a subdirectory of a project silently creates a
second cache. This module picks the base directory for *default* state paths:

1. ``DOCPULL_HOME`` when set (explicit override),
2. the nearest ancestor directory containing ``docpull.yaml``,
3. otherwise the current working directory (the historical behavior).

Only defaults go through here. Paths a user passes explicitly keep normal
cwd-relative semantics. When the base is the current directory the relative
path is returned unchanged so existing output and fingerprints stay stable.
"""

from __future__ import annotations

import os
from pathlib import Path

DOCPULL_HOME_ENV = "DOCPULL_HOME"
PROJECT_CONFIG_FILENAME = "docpull.yaml"


def discover_project_root(start: Path | None = None) -> Path | None:
    """Return the nearest directory at or above ``start`` holding ``docpull.yaml``."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / PROJECT_CONFIG_FILENAME).is_file():
            return directory
    return None


def state_base_dir(start: Path | None = None) -> Path | None:
    """Return the base for default state paths, or ``None`` for plain cwd."""
    home = os.environ.get(DOCPULL_HOME_ENV, "").strip()
    if home:
        return Path(home).expanduser().resolve()
    return discover_project_root(start)


def resolve_default_state_path(relative: Path | str, start: Path | None = None) -> Path:
    """Anchor a default relative state path to ``DOCPULL_HOME`` or the project root."""
    path = Path(relative)
    if path.is_absolute():
        return path
    base = state_base_dir(start)
    if base is None or base == (start or Path.cwd()).resolve():
        return path
    return base / path

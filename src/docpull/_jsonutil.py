"""Shared JSON file and value helpers for internal modules."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def read_json(path: Path, *, error: type[Exception], required: bool = True, default: Any = None) -> Any:
    """Load JSON from ``path``; a missing optional file returns ``default``."""
    if not path.exists():
        if required:
            raise error(f"Missing required file: {path}")
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise error(f"Invalid JSON in {path}: {err}") from err


def write_json(path: Path, payload: Any, *, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=sort_keys)
    path.write_text(text + "\n", encoding="utf-8")


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def string_list(value: Any) -> list[str]:
    """Return the non-blank, stripped items of a list; anything else is empty."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def slugify(value: str, *, fallback: str) -> str:
    slug = _SLUG_RE.sub("-", value).strip("-").lower()
    return slug[:80].strip("-") or fallback

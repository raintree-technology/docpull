"""Small integrity primitives shared by benchmark trust boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, NoReturn

from .models import PortableReport


def strict_json_loads(payload: str | bytes) -> Any:
    """Parse JSON while rejecting ambiguous duplicate object keys."""

    return json.loads(
        payload,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_nonstandard_constant,
    )


def strict_json_file(path: Path) -> Any:
    return strict_json_loads(path.read_bytes())


def load_portable_report(path: Path) -> PortableReport:
    return PortableReport.model_validate(strict_json_file(path))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant: {value}")

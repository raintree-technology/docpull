"""Shared plain-dict record iteration for framework loaders.

Both framework loaders converge on :func:`iter_pack_records` so tests and
callers can inspect record shapes without installing LangChain or LlamaIndex.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..pack_reader import PackReadError, load_pack


def iter_pack_records(pack_dir: Path | str) -> Iterator[dict[str, Any]]:
    """Yield one plain record dict per pack record in stable pack order.

    Prefers ``documents.ndjson``/``documents.jsonl`` and falls back to
    ``corpus.manifest.json`` records whose ``output_path`` files exist on
    disk. Each yielded dict has ``content`` plus a ``metadata`` mapping with
    ``url``, ``title``, ``document_id``, ``chunk_id``, ``content_hash``,
    ``token_count``, and ``source``. Chunked and whole-document records are
    yielded as-is, one dict per record. No network access.
    """
    root = Path(pack_dir).expanduser().resolve()
    jsonl_path = root / "documents.jsonl"
    if jsonl_path.is_file() and not (root / "documents.ndjson").is_file():
        yield from _iter_jsonl_records(jsonl_path)
        return
    pack = load_pack(root)
    for record in pack.documents:
        yield _plain_record(
            content=record.content,
            url=record.url,
            title=record.title,
            document_id=record.document_id,
            chunk_id=record.chunk_id,
            content_hash=record.content_hash,
            token_count=record.token_count,
        )


def _iter_jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as err:
            raise PackReadError(f"Invalid NDJSON in {path} line {index}: {err}") from err
        if not isinstance(data, dict):
            raise PackReadError(f"Invalid NDJSON in {path} line {index}: expected object")
        url = str(data.get("url") or "")
        if not url:
            raise PackReadError(f"Document record in {path} line {index} is missing url")
        content = str(data.get("content") or "")
        content_hash = str(data.get("content_hash") or "").strip()
        if not content_hash:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_id = str(data.get("document_id") or "") or _stable_id("doc", url, content_hash)
        yield _plain_record(
            content=content,
            url=url,
            title=_optional_str(data.get("title")),
            document_id=document_id,
            chunk_id=_optional_str(data.get("chunk_id")),
            content_hash=content_hash,
            token_count=_optional_int(data.get("token_count")),
        )


def _plain_record(
    *,
    content: str,
    url: str,
    title: str | None,
    document_id: str,
    chunk_id: str | None,
    content_hash: str,
    token_count: int | None,
) -> dict[str, Any]:
    return {
        "content": content,
        "metadata": {
            "url": url,
            "title": title,
            "document_id": document_id,
            "chunk_id": chunk_id,
            "content_hash": content_hash,
            "token_count": token_count,
            "source": url,
        },
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"

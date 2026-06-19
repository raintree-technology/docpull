"""Reusable test fixtures for local context packs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_context_pack(
    pack_dir: Path,
    *,
    records: list[dict[str, Any]] | None = None,
    include_domains: list[str] | None = None,
    provider: str = "parallel",
    objective: str = "Review Parallel Search API",
) -> list[dict[str, Any]]:
    """Write a minimal docpull-compatible context pack for tests."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = pack_dir / "sources"
    sources_dir.mkdir(exist_ok=True)
    domains = include_domains or ["docs.parallel.ai"]
    pack_records = records or [
        {
            "document_id": "doc_1",
            "url": "https://docs.parallel.ai/api-reference/search/search",
            "title": "Parallel Search API",
            "content": "Parallel Search API returns cited JSON results for live agent search.",
            "content_hash": "hash_1",
            "source_type": "parallel_extract",
        }
    ]

    sources = []
    for index, record in enumerate(pack_records, start=1):
        source_path = sources_dir / f"{index:02d}.md"
        source_path.write_text(str(record["content"]), encoding="utf-8")
        sources.append(
            {
                "index": index,
                "url": record["url"],
                "title": record["title"],
                "path": f"sources/{index:02d}.md",
            }
        )

    (pack_dir / "documents.ndjson").write_text(
        "\n".join(json.dumps(record) for record in pack_records) + "\n",
        encoding="utf-8",
    )
    (pack_dir / "corpus.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "document_count": len({record["document_id"] for record in pack_records}),
                "record_count": len(pack_records),
                "records": [
                    {
                        "document_id": record["document_id"],
                        "url": record["url"],
                        "content_hash": record["content_hash"],
                    }
                    for record in pack_records
                ],
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "sources.md").write_text("# Sources\n", encoding="utf-8")
    (pack_dir / f"{provider}.pack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": provider,
                "workflow": "context-pack",
                "objective": objective,
                "request_options": {"source_policy": {"include_domains": domains}},
                "extract_error_count": 0,
                "record_count": len(pack_records),
                "sources": sources,
                "artifacts": {
                    "documents_ndjson": "documents.ndjson",
                    "corpus_manifest": "corpus.manifest.json",
                    "sources": "sources.md",
                },
            }
        ),
        encoding="utf-8",
    )
    return pack_records

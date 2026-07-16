"""Build local v3 packs from bounded dataset summaries."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from .common import ContextPackError, write_json
from .typed import (
    PrepareLevel,
    TypedPackItem,
    read_https_text,
    simple_summary_markdown,
    write_typed_pack,
)
from .typed_models import DatasetSchemaArtifact

DATASET_WORKFLOW = "dataset-pack"
DEFAULT_DATASET_OUTPUT_DIR = Path("packs/dataset")
MAX_SAMPLE_ROWS = 20


def build_dataset_pack(
    sources: list[str | Path],
    *,
    output_dir: Path = DEFAULT_DATASET_OUTPUT_DIR,
    max_items: int = 50,
    chunk_tokens: int = 4000,
    prepare_level: PrepareLevel = "raw",
) -> dict[str, Any]:
    """Summarize local datasets and bounded remote HTTPS JSON/CSV snapshots."""
    if not sources:
        raise ContextPackError("dataset-pack requires at least one local file or HTTPS JSON/CSV URL.")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[TypedPackItem] = []
    schemas: list[dict[str, Any]] = []
    for source in sources[:max_items]:
        source_text = str(source)
        if _is_remote_source(source_text):
            summaries = [_remote_summary(source_text)]
            for summary in summaries:
                schemas.append(summary)
                items.append(_item_for_summary(None, summary, source_url=source_text))
            if len(items) >= max_items:
                break
            continue
        path = Path(source).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ContextPackError(f"Dataset source file does not exist: {path}")
        summaries = _summaries_for_path(path, max_items=max_items - len(items))
        for summary in summaries:
            schemas.append(summary)
            items.append(_item_for_summary(path, summary, source_url=path.as_uri()))
            if len(items) >= max_items:
                break
        if len(items) >= max_items:
            break
    if not items:
        raise ContextPackError("No readable dataset tables or documents were found.")

    schema_path = output_dir / "dataset.schema.json"
    schema_payload = DatasetSchemaArtifact(
        workflow=DATASET_WORKFLOW,
        source_count=len(sources),
        item_count=len(items),
        datasets=schemas,
    )
    write_json(schema_path, schema_payload.model_dump(mode="json"))
    return write_typed_pack(
        workflow=DATASET_WORKFLOW,
        output_format="dataset",
        output_dir=output_dir,
        items=items,
        pack_filename="dataset.pack.json",
        index_filename="dataset.index.json",
        items_filename="dataset.items.ndjson",
        summary_filename="DATASET.md",
        index_payload={"datasets": schemas},
        summary_markdown=simple_summary_markdown(
            title="Dataset Pack",
            source=", ".join(str(source) for source in sources),
            items=items,
        ),
        result_summary={"dataset_count": len(schemas)},
        chunk_tokens=chunk_tokens,
        extra_artifacts={"schema": schema_path},
        prepare_level=prepare_level,
    )


async def async_build_dataset_pack(
    sources: list[str | Path],
    **kwargs: Any,
) -> dict[str, Any]:
    """Async-compatible wrapper for SDK callers already inside an event loop."""
    return await asyncio.to_thread(build_dataset_pack, sources, **kwargs)


def _summaries_for_path(path: Path, *, max_items: int) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [_delimited_summary(path, delimiter=",")]
    if suffix == ".tsv":
        return [_delimited_summary(path, delimiter="\t")]
    if suffix in {".json"}:
        return [_json_summary(path)]
    if suffix in {".jsonl", ".ndjson"}:
        return [_ndjson_summary(path)]
    if suffix in {".sqlite", ".sqlite3", ".db"}:
        return _sqlite_summaries(path, max_items=max_items)
    if suffix == ".parquet":
        return [_parquet_summary(path)]
    raise ContextPackError(f"Unsupported dataset file type: {path.suffix or path.name}")


def _is_remote_source(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _remote_summary(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    accept = "application/json,text/csv;q=0.9" if suffix == ".json" else "text/csv,application/json;q=0.9"
    try:
        response = read_https_text(url, accept=accept, max_bytes=10_000_000)
    except ValueError as err:
        raise ContextPackError(str(err)) from err
    content_type = response.content_type.split(";", 1)[0].strip().lower()
    snapshot_hash = hashlib.sha256(response.text.encode("utf-8")).hexdigest()
    provenance = {
        "original_url": url,
        "resolved_url": response.url,
        "query_parameters": [
            {"name": name, "value": value} for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        "snapshot_hash": snapshot_hash,
        "hash_algorithm": "sha256",
        "content_type": response.content_type,
        "http_status": response.status_code,
    }
    name = Path(parsed.path).name or parsed.netloc
    if suffix == ".json" or "json" in content_type:
        data = json.loads(response.text)
        if isinstance(data, list):
            json_rows = [row for row in data[:MAX_SAMPLE_ROWS] if isinstance(row, dict)]
            summary = _tabular_summary(
                path=None,
                kind="json",
                name=name,
                rows=json_rows,
                fieldnames=sorted({key for row in json_rows for key in row}),
                row_count=len(data),
                source=url,
            )
        elif isinstance(data, dict):
            summary = {
                "kind": "json",
                "name": name,
                "path": url,
                "row_count": 1,
                "columns": [
                    {"name": key, "types": [type(value).__name__], "null_count": int(value is None)}
                    for key, value in sorted(data.items())
                ],
                "sample": data,
            }
        else:
            raise ContextPackError(f"Remote JSON dataset must be an object or array: {url}")
    elif suffix == ".csv" or content_type in {"text/csv", "application/csv"}:
        reader = csv.DictReader(io.StringIO(response.text))
        csv_rows: list[dict[str, Any]] = []
        row_count = 0
        for row in reader:
            row_count += 1
            if len(csv_rows) < MAX_SAMPLE_ROWS:
                csv_rows.append(dict(row))
        summary = _tabular_summary(
            path=None,
            kind="csv",
            name=name,
            rows=csv_rows,
            fieldnames=list(reader.fieldnames or []),
            row_count=row_count,
            source=url,
        )
    else:
        raise ContextPackError(
            f"Remote dataset must be HTTPS JSON or CSV; received {response.content_type or 'unknown'}: {url}"
        )
    summary["provenance"] = provenance
    summary["snapshot_hash"] = snapshot_hash
    return summary


def _delimited_summary(path: Path, *, delimiter: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    row_count = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            row_count += 1
            if len(rows) < MAX_SAMPLE_ROWS:
                rows.append(row)
    return _tabular_summary(
        path=path,
        kind="csv" if delimiter == "," else "tsv",
        name=path.name,
        rows=rows,
        fieldnames=fieldnames,
        row_count=row_count,
    )


def _json_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rows = [row for row in data[:MAX_SAMPLE_ROWS] if isinstance(row, dict)]
        fieldnames = sorted({key for row in rows for key in row})
        return _tabular_summary(
            path=path, kind="json", name=path.name, rows=rows, fieldnames=fieldnames, row_count=len(data)
        )
    if isinstance(data, dict):
        return {
            "kind": "json",
            "name": path.name,
            "path": str(path),
            "row_count": 1,
            "columns": [
                {"name": key, "types": [type(value).__name__], "null_count": int(value is None)}
                for key, value in sorted(data.items())
            ],
            "sample": data,
        }
    raise ContextPackError(f"JSON dataset must be an object or array: {path}")


def _ndjson_summary(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    row_count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row_count += 1
        if len(rows) < MAX_SAMPLE_ROWS:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
    fieldnames = sorted({key for row in rows for key in row})
    return _tabular_summary(
        path=path, kind="ndjson", name=path.name, rows=rows, fieldnames=fieldnames, row_count=row_count
    )


def _sqlite_summaries(path: Path, *, max_items: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    with sqlite3.connect(path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table_name,) in tables[:max_items]:
            table_identifier = _sqlite_identifier(str(table_name))
            columns = connection.execute(f"PRAGMA table_info({table_identifier})").fetchall()
            row_count = int(
                connection.execute(f"SELECT COUNT(*) FROM {table_identifier}").fetchone()[0]  # nosec B608
            )
            cursor = connection.execute(
                f"SELECT * FROM {table_identifier} LIMIT ?",  # nosec B608
                (MAX_SAMPLE_ROWS,),
            )
            names = [description[0] for description in cursor.description or []]
            rows = [dict(zip(names, row, strict=False)) for row in cursor.fetchall()]
            summary = _tabular_summary(
                path=path,
                kind="sqlite",
                name=table_name,
                rows=rows,
                fieldnames=names,
                row_count=row_count,
            )
            summary["sqlite_columns"] = [
                {
                    "name": column[1],
                    "declared_type": column[2],
                    "nullable": not bool(column[3]),
                    "primary_key": bool(column[5]),
                }
                for column in columns
            ]
            summaries.append(summary)
    return summaries


def _sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _parquet_summary(path: Path) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ImportError as err:
        raise ContextPackError("Parquet dataset support requires `pip install 'docpull[parquet]'`.") from err
    table = pq.read_table(path)
    rows = table.slice(0, MAX_SAMPLE_ROWS).to_pylist()
    fieldnames = [field.name for field in table.schema]
    summary = _tabular_summary(
        path=path, kind="parquet", name=path.name, rows=rows, fieldnames=fieldnames, row_count=table.num_rows
    )
    summary["parquet_schema"] = str(table.schema)
    return summary


def _tabular_summary(
    *,
    path: Path | None,
    kind: str,
    name: str,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    row_count: int | None,
    source: str | None = None,
) -> dict[str, Any]:
    columns = []
    for field in fieldnames:
        values = [row.get(field) for row in rows]
        type_counts = Counter(type(value).__name__ for value in values if value is not None)
        columns.append(
            {
                "name": field,
                "types": sorted(type_counts) or ["unknown"],
                "null_count": sum(1 for value in values if _is_blank(value)),
                "sample_values": [value for value in values if not _is_blank(value)][:5],
            }
        )
    return {
        "kind": kind,
        "name": name,
        "path": source or str(path),
        "row_count": row_count if row_count is not None else None,
        "sample_row_count": len(rows),
        "column_count": len(fieldnames),
        "columns": columns,
        "sample": rows[:5],
    }


def _item_for_summary(
    path: Path | None,
    summary: dict[str, Any],
    *,
    source_url: str,
) -> TypedPackItem:
    markdown = _summary_markdown(summary)
    parsed = urlparse(source_url)
    source_name = Path(parsed.path).name if parsed.scheme else Path(summary["path"]).name
    title = f"{source_name or parsed.netloc}: {summary['name']}"
    provenance_raw = summary.get("provenance")
    provenance: dict[str, Any] = provenance_raw if isinstance(provenance_raw, dict) else {}
    return TypedPackItem(
        title=title,
        url=source_url,
        markdown=markdown,
        source_type="dataset",
        item_kind=str(summary["kind"]),
        metadata={
            "dataset_path": str(path) if path else None,
            "dataset_url": source_url if path is None else None,
            "dataset_name": summary["name"],
            "dataset_kind": summary["kind"],
            "snapshot_hash": summary.get("snapshot_hash"),
            "provenance": provenance,
        },
        route={
            "source_kind": "https" if path is None else "file",
            "source_url": source_url,
            "original_url": provenance.get("original_url"),
            "resolved_url": provenance.get("resolved_url"),
        },
        public={"row_count": summary.get("row_count"), "column_count": summary.get("column_count")},
    )


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Dataset: {summary['name']}",
        "",
        f"- Kind: `{summary['kind']}`",
        f"- Path: `{summary['path']}`",
        f"- Rows: {summary.get('row_count') if summary.get('row_count') is not None else 'unknown'}",
        f"- Columns: {summary.get('column_count', 0)}",
        "",
        "## Columns",
        "",
    ]
    for column in summary.get("columns", []):
        types = ", ".join(column.get("types", []))
        null_count = column.get("null_count", 0)
        lines.append(f"- `{column['name']}`: types={types}; nulls={null_count}")
    if summary.get("sample"):
        lines.extend(
            [
                "",
                "## Bounded Sample",
                "",
                "```json",
                json.dumps(summary["sample"], indent=2, ensure_ascii=False),
                "```",
            ]
        )
    return "\n".join(lines)


def _is_blank(value: Any) -> bool:
    return value is None or value == ""


__all__ = ["DEFAULT_DATASET_OUTPUT_DIR", "async_build_dataset_pack", "build_dataset_pack"]

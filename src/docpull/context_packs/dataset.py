"""Build local v3 packs from bounded dataset summaries."""

from __future__ import annotations

import asyncio
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .common import ContextPackError, write_json
from .typed import PrepareLevel, TypedPackItem, simple_summary_markdown, write_typed_pack
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
    """Summarize local CSV/TSV/JSON/NDJSON/SQLite/Parquet datasets as a v3 pack."""
    if not sources:
        raise ContextPackError("dataset-pack requires at least one local file.")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[TypedPackItem] = []
    schemas: list[dict[str, Any]] = []
    for source in sources[:max_items]:
        path = Path(source).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ContextPackError(f"Dataset source file does not exist: {path}")
        summaries = _summaries_for_path(path, max_items=max_items - len(items))
        for summary in summaries:
            schemas.append(summary)
            items.append(_item_for_summary(path, summary))
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
            source=", ".join(str(Path(source)) for source in sources),
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
    path: Path,
    kind: str,
    name: str,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    row_count: int | None,
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
        "path": str(path),
        "row_count": row_count if row_count is not None else None,
        "sample_row_count": len(rows),
        "column_count": len(fieldnames),
        "columns": columns,
        "sample": rows[:5],
    }


def _item_for_summary(path: Path, summary: dict[str, Any]) -> TypedPackItem:
    markdown = _summary_markdown(summary)
    title = f"{Path(summary['path']).name}: {summary['name']}"
    return TypedPackItem(
        title=title,
        url=path.as_uri(),
        markdown=markdown,
        source_type="dataset",
        item_kind=str(summary["kind"]),
        metadata={
            "dataset_path": str(path),
            "dataset_name": summary["name"],
            "dataset_kind": summary["kind"],
        },
        route={"source_kind": "file", "source_url": path.as_uri()},
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

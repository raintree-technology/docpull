"""Build local v3 packs from transcript files or transcript URLs."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..http.client import AsyncHttpClient
from ..http.rate_limiter import PerHostRateLimiter
from ..security.robots import RobotsChecker
from ..security.url_validator import UrlValidator
from .common import ContextPackError, write_json
from .typed import PrepareLevel, TypedPackItem, simple_summary_markdown, write_typed_pack
from .typed_models import TranscriptMetadataArtifact

TRANSCRIPT_WORKFLOW = "transcript-pack"
DEFAULT_TRANSCRIPT_OUTPUT_DIR = Path("packs/transcript")
MAX_TRANSCRIPT_BYTES = 5_000_000
_TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3}|\d{1,2}:\d{2}[,.]\d{3})\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3}|\d{1,2}:\d{2}[,.]\d{3})"
)


@dataclass(frozen=True)
class TranscriptSource:
    text: str
    source_url: str
    source_kind: str
    content_type: str


def build_transcript_pack(
    sources: list[str | Path],
    *,
    output_dir: Path = DEFAULT_TRANSCRIPT_OUTPUT_DIR,
    max_items: int = 200,
    chunk_tokens: int = 4000,
    prepare_level: PrepareLevel = "raw",
) -> dict[str, Any]:
    """Normalize transcript segments into a v3 context pack."""
    if not sources:
        raise ContextPackError("transcript-pack requires at least one source.")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[TypedPackItem] = []
    segments: list[dict[str, Any]] = []
    for source in sources:
        transcript = _read_source(source)
        parsed = _parse_segments(transcript.text, source_url=transcript.source_url)
        for segment in parsed:
            segments.append(segment)
            items.append(_item_for_segment(transcript, segment, len(segments)))
            if len(items) >= max_items:
                break
        if len(items) >= max_items:
            break
    if not items:
        raise ContextPackError("No transcript segments were found.")

    segments_path = output_dir / "transcript.segments.ndjson"
    segments_path.write_text(
        "".join(json.dumps(segment, ensure_ascii=False, sort_keys=True) + "\n" for segment in segments),
        encoding="utf-8",
    )

    write_json(
        output_dir / "transcript.metadata.json",
        TranscriptMetadataArtifact(segment_count=len(segments)).model_dump(mode="json"),
    )
    return write_typed_pack(
        workflow=TRANSCRIPT_WORKFLOW,
        output_format="transcript",
        output_dir=output_dir,
        items=items,
        pack_filename="transcript.pack.json",
        index_filename="transcript.index.json",
        items_filename="transcript.items.ndjson",
        summary_filename="TRANSCRIPT.md",
        index_payload={"segment_count": len(segments)},
        summary_markdown=simple_summary_markdown(
            title="Transcript Pack",
            source=", ".join(str(source) for source in sources),
            items=items,
        ),
        result_summary={"segment_count": len(segments)},
        chunk_tokens=chunk_tokens,
        extra_artifacts={"segments": segments_path, "metadata": output_dir / "transcript.metadata.json"},
        prepare_level=prepare_level,
    )


async def async_build_transcript_pack(
    sources: list[str | Path],
    **kwargs: Any,
) -> dict[str, Any]:
    """Async-compatible wrapper for SDK callers already inside an event loop."""
    return await asyncio.to_thread(build_transcript_pack, sources, **kwargs)


def _read_source(source: str | Path) -> TranscriptSource:
    value = str(source)
    if value.startswith(("http://", "https://")):
        return _read_remote(value)
    path = Path(source).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ContextPackError(f"Transcript source file does not exist: {path}")
    data = path.read_bytes()
    if len(data) > MAX_TRANSCRIPT_BYTES:
        raise ContextPackError(f"Transcript source exceeds {MAX_TRANSCRIPT_BYTES} bytes: {path}")
    return TranscriptSource(
        text=data.decode("utf-8", errors="replace"),
        source_url=path.as_uri(),
        source_kind="file",
        content_type="text/plain",
    )


def _read_remote(url: str) -> TranscriptSource:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_read_remote_async(url))
    raise ContextPackError("Remote transcript-pack sources cannot be fetched while an event loop is running.")


async def _read_remote_async(url: str) -> TranscriptSource:
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(url)
    if not validation.is_valid:
        raise ContextPackError(f"Remote transcript source rejected: {validation.rejection_reason}")
    rate_limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)
    async with AsyncHttpClient(
        rate_limiter=rate_limiter, url_validator=validator, max_content_size=MAX_TRANSCRIPT_BYTES
    ) as client:
        robots = RobotsChecker(user_agent=client.user_agent, url_validator=validator)
        if not robots.is_allowed(url):
            raise ContextPackError(
                f"Robots.txt disallows or could not verify remote transcript source: {url}"
            )
        response = await client.get(url, headers={"Accept": "text/vtt, text/plain, application/json, */*"})
    if response.status_code >= 400:
        raise ContextPackError(f"Could not fetch transcript source {url}: HTTP {response.status_code}")
    return TranscriptSource(
        text=response.content.decode("utf-8", errors="replace"),
        source_url=response.url,
        source_kind="remote",
        content_type=response.content_type,
    )


def _parse_segments(text: str, *, source_url: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("[") or stripped.startswith("{"):
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            raw_segments = parsed.get("segments") or parsed.get("items") or []
        else:
            raw_segments = parsed
        if isinstance(raw_segments, list):
            return [
                _segment_from_json(item, index, source_url)
                for index, item in enumerate(raw_segments, start=1)
                if isinstance(item, dict)
            ]
    if "-->" in stripped:
        return _parse_timed_text(stripped, source_url=source_url)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", stripped) if block.strip()]
    return [
        {
            "schema_version": 3,
            "segment_id": f"segment_{index:04d}",
            "index": index,
            "text": block,
            "source_url": source_url,
        }
        for index, block in enumerate(blocks, start=1)
    ]


def _parse_timed_text(text: str, *, source_url: str) -> list[dict[str, Any]]:
    lines = [line.strip("\ufeff") for line in text.splitlines()]
    segments: list[dict[str, Any]] = []
    index = 0
    cursor = 0
    while cursor < len(lines):
        match = _TIMING_RE.search(lines[cursor])
        if not match:
            cursor += 1
            continue
        start = _normalize_timestamp(match.group("start"))
        end = _normalize_timestamp(match.group("end"))
        cursor += 1
        text_lines: list[str] = []
        while cursor < len(lines) and lines[cursor].strip():
            text_lines.append(_strip_vtt_markup(lines[cursor].strip()))
            cursor += 1
        content = " ".join(line for line in text_lines if line).strip()
        if content:
            index += 1
            segments.append(
                {
                    "schema_version": 3,
                    "segment_id": f"segment_{index:04d}",
                    "index": index,
                    "start": start,
                    "end": end,
                    "text": content,
                    "source_url": source_url,
                }
            )
        cursor += 1
    return segments


def _segment_from_json(item: dict[str, Any], index: int, source_url: str) -> dict[str, Any]:
    text = str(item.get("text") or item.get("content") or item.get("transcript") or "").strip()
    return {
        "schema_version": 3,
        "segment_id": str(item.get("id") or f"segment_{index:04d}"),
        "index": index,
        "start": item.get("start") or item.get("start_time"),
        "end": item.get("end") or item.get("end_time"),
        "speaker": item.get("speaker"),
        "text": text,
        "source_url": source_url,
    }


def _item_for_segment(source: TranscriptSource, segment: dict[str, Any], index: int) -> TypedPackItem:
    timestamp = ""
    if segment.get("start") or segment.get("end"):
        timestamp = f" ({segment.get('start') or '?'}-{segment.get('end') or '?'})"
    title = f"Transcript segment {index}{timestamp}"
    markdown = "\n".join(
        [
            "# " + title,
            "",
            f"- Source: {source.source_url}",
            f"- Segment: {segment.get('segment_id')}",
            f"- Start: {segment.get('start') or 'unknown'}",
            f"- End: {segment.get('end') or 'unknown'}",
            "",
            str(segment.get("text") or ""),
        ]
    )
    return TypedPackItem(
        title=title,
        url=f"{source.source_url}#segment-{index}",
        markdown=markdown,
        source_type="transcript_segment",
        item_kind="segment",
        metadata={"segment": segment, "source_url": source.source_url, "source_kind": source.source_kind},
        route={"source_kind": source.source_kind, "source_url": source.source_url},
        public={"start": segment.get("start"), "end": segment.get("end"), "speaker": segment.get("speaker")},
    )


def _normalize_timestamp(value: str) -> str:
    parts = value.replace(",", ".")
    if len(parts.split(":")) == 2:
        return "00:" + parts
    return parts


def _strip_vtt_markup(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


__all__ = ["DEFAULT_TRANSCRIPT_OUTPUT_DIR", "async_build_transcript_pack", "build_transcript_pack"]

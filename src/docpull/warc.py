"""Minimal WARC/1.1 writer for archival capture of raw HTTP responses.

Implemented against the WARC 1.1 specification (ISO 28500) without external
dependencies. Each record is written as its own gzip member, per the
``.warc.gz`` convention, so standard tools can seek record boundaries.
"""

from __future__ import annotations

import gzip
import hashlib
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from http.client import responses as _http_reason_phrases
from pathlib import Path
from typing import IO, TYPE_CHECKING

from . import __version__
from .time_utils import utc_now

if TYPE_CHECKING:
    from .pipeline.base import EventEmitter, PageContext

WARC_FILENAME = "capture.warc.gz"

_CRLF = "\r\n"

# Response headers never persisted to the archive: cookies (credentials),
# authentication material, and hop-by-hop headers that describe the original
# transfer rather than the stored payload. Content-Encoding/Content-Length are
# dropped too because the stored body is the decoded payload; a correct
# Content-Length is re-emitted over the stored bytes.
_STRIPPED_RESPONSE_HEADERS = frozenset(
    {
        "set-cookie",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "authorization",
        "www-authenticate",
        "te",
        "trailer",
        "content-encoding",
        "content-length",
    }
)


def _sanitize_header_text(value: str) -> str:
    """Remove CR/LF/NUL so untrusted header data cannot forge record lines."""
    return value.replace("\r", "").replace("\n", "").replace("\x00", "").strip()


def _new_record_id() -> str:
    return f"<urn:uuid:{uuid.uuid4()}>"


def _warc_date(moment: datetime) -> str:
    """Format a datetime as the UTC ``Z``-suffixed timestamp WARC requires."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_record(warc_headers: list[tuple[str, str]], block: bytes) -> bytes:
    lines = ["WARC/1.1"]
    lines.extend(f"{name}: {value}" for name, value in warc_headers)
    lines.append(f"Content-Length: {len(block)}")
    header_bytes = (_CRLF.join(lines) + _CRLF + _CRLF).encode("utf-8")
    return header_bytes + block + _CRLF.encode("ascii") + _CRLF.encode("ascii")


class WarcWriter:
    """Append WARC/1.1 records to a ``.warc.gz`` file, one gzip member each."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: IO[bytes] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_open(self) -> IO[bytes]:
        """Open the target lazily; start a brand-new file with a warcinfo record."""
        if self._fh is not None:
            return self._fh
        self._path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self._path.exists() or self._path.stat().st_size == 0
        self._fh = self._path.open("ab")
        if is_new:
            self._write_warcinfo()
        return self._fh

    def _write_warcinfo(self) -> None:
        fields = f"software: docpull/{__version__}{_CRLF}format: WARC File Format 1.1{_CRLF}"
        record = _build_record(
            [
                ("WARC-Type", "warcinfo"),
                ("WARC-Record-ID", _new_record_id()),
                ("WARC-Date", _warc_date(utc_now())),
                ("WARC-Filename", self._path.name),
                ("Content-Type", "application/warc-fields"),
            ],
            fields.encode("utf-8"),
        )
        self._append(record)

    def _append(self, record: bytes) -> None:
        fh = self._fh
        if fh is None:  # pragma: no cover - _ensure_open precedes every append
            raise RuntimeError("WarcWriter is not open")
        fh.write(gzip.compress(record))
        fh.flush()

    def write_response(
        self,
        url: str,
        status_code: int,
        headers: dict[str, str],
        body: bytes,
        fetched_at: datetime,
    ) -> str:
        """Append one ``response`` record and return its WARC-Record-ID."""
        self._ensure_open()
        reason = _http_reason_phrases.get(status_code, "")
        status_line = f"HTTP/1.1 {status_code} {reason}".rstrip()
        http_lines = [status_line]
        for raw_name, raw_value in headers.items():
            name = _sanitize_header_text(raw_name)
            if not name or name.lower() in _STRIPPED_RESPONSE_HEADERS or name.lower().startswith("proxy-"):
                continue
            http_lines.append(f"{name}: {_sanitize_header_text(raw_value)}")
        http_lines.append(f"Content-Length: {len(body)}")
        block = (_CRLF.join(http_lines) + _CRLF + _CRLF).encode("utf-8") + body

        record_id = _new_record_id()
        record = _build_record(
            [
                ("WARC-Type", "response"),
                ("WARC-Record-ID", record_id),
                ("WARC-Date", _warc_date(fetched_at)),
                ("WARC-Target-URI", _sanitize_header_text(url)),
                ("WARC-Payload-Digest", payload_digest(body)),
                ("Content-Type", "application/http;msgtype=response"),
            ],
            block,
        )
        self._append(record)
        return record_id

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def payload_digest(body: bytes) -> str:
    """``sha256:<hex>`` digest of the exact stored payload bytes."""
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def read_warc_records(path: Path) -> Iterator[tuple[dict[str, str], bytes]]:
    """Yield ``(warc_headers, block_bytes)`` for each record in a ``.warc.gz`` file.

    ``gzip`` transparently reads concatenated members, so this works for both
    single-member and member-per-record files.
    """
    with gzip.open(path, "rb") as fh:
        data = fh.read()
    pos = 0
    while pos < len(data):
        header_end = data.index(b"\r\n\r\n", pos)
        header_lines = data[pos:header_end].decode("utf-8").split(_CRLF)
        if not header_lines[0].startswith("WARC/"):
            raise ValueError(f"Malformed WARC record at byte {pos}: {header_lines[0]!r}")
        headers: dict[str, str] = {}
        for line in header_lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip()] = value.strip()
        block_start = header_end + 4
        block_length = int(headers["Content-Length"])
        block = data[block_start : block_start + block_length]
        yield headers, block
        # Skip the two CRLFs terminating the record.
        pos = block_start + block_length + 4


class WarcWriteStep:
    """Pipeline step that archives the raw HTTP response captured by FetchStep.

    Runs after conversion (so record IDs never leak into rendered Markdown or
    frontmatter) and before save (so manifest records carry the linkage via
    ``ctx.metadata``). Pages without raw response bytes — cache 304 skips and
    browser-rendered pages — are passed through untouched.
    """

    name = "warc"

    def __init__(self, writer: WarcWriter) -> None:
        self._writer = writer

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        if ctx.raw_content is None or ctx.raw_response_headers is None or ctx.status_code is None:
            return ctx
        record_id = self._writer.write_response(
            url=ctx.url,
            status_code=ctx.status_code,
            headers=ctx.raw_response_headers,
            body=ctx.raw_content,
            fetched_at=utc_now(),
        )
        ctx.warc_record_id = record_id
        ctx.metadata["warc_record_id"] = record_id
        ctx.metadata["raw_content_hash"] = payload_digest(ctx.raw_content)
        return ctx

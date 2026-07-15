"""Download safety policy for web-source fetches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse


class UnsafeDownloadError(ValueError):
    """Raised when a response looks like a file download, not text/page content."""


ALLOWED_DOCUMENT_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/xml",
        "application/xml",
        "application/atom+xml",
        "application/rss+xml",
        "application/json",
        "application/ld+json",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
    }
)


_DANGEROUS_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "application/x-msdownload",
        "application/vnd.microsoft.portable-executable",
        "application/x-msdos-program",
        "application/x-dosexec",
        "application/x-executable",
        "application/x-mach-binary",
        "application/x-elf",
        "application/java-archive",
        "application/x-sh",
        "application/x-csh",
        "application/javascript",
        "text/javascript",
        "application/pdf",
        "image/svg+xml",
        "application/zip",
        "application/x-zip-compressed",
        "application/x-7z-compressed",
        "application/vnd.rar",
        "application/x-rar-compressed",
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/zstd",
        "application/vnd.android.package-archive",
        "application/vnd.apple.installer+xml",
        "application/vnd.ms-cab-compressed",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)


_DANGEROUS_FILE_EXTENSIONS = frozenset(
    {
        ".7z",
        ".apk",
        ".app",
        ".appimage",
        ".bat",
        ".bin",
        ".bz2",
        ".cab",
        ".class",
        ".cmd",
        ".com",
        ".deb",
        ".dll",
        ".dmg",
        ".doc",
        ".docx",
        ".dylib",
        ".ear",
        ".elf",
        ".exe",
        ".gz",
        ".gif",
        ".heic",
        ".heif",
        ".ico",
        ".ipa",
        ".iso",
        ".jar",
        ".jpeg",
        ".jpg",
        ".js",
        ".m4a",
        ".mjs",
        ".mov",
        ".mp3",
        ".mp4",
        ".msi",
        ".msp",
        ".otf",
        ".pdf",
        ".pkg",
        ".png",
        ".ppt",
        ".pptx",
        ".ps1",
        ".rar",
        ".rpm",
        ".run",
        ".scr",
        ".so",
        ".svg",
        ".tar",
        ".tgz",
        ".ttf",
        ".vbs",
        ".war",
        ".wasm",
        ".wav",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".wsf",
        ".xls",
        ".xlsx",
        ".xz",
        ".zip",
        ".zst",
    }
)


@dataclass(frozen=True)
class _MagicSignature:
    name: str
    prefix: bytes
    offset: int = 0


_DANGEROUS_MAGIC_SIGNATURES = (
    _MagicSignature("Windows executable", b"MZ"),
    _MagicSignature("ELF executable", b"\x7fELF"),
    _MagicSignature("Mach-O executable", b"\xfe\xed\xfa\xce"),
    _MagicSignature("Mach-O executable", b"\xce\xfa\xed\xfe"),
    _MagicSignature("Mach-O executable", b"\xfe\xed\xfa\xcf"),
    _MagicSignature("Mach-O executable", b"\xcf\xfa\xed\xfe"),
    _MagicSignature("Mach-O universal binary", b"\xca\xfe\xba\xbe"),
    _MagicSignature("ZIP/archive payload", b"PK\x03\x04"),
    _MagicSignature("ZIP/archive payload", b"PK\x05\x06"),
    _MagicSignature("ZIP/archive payload", b"PK\x07\x08"),
    _MagicSignature("RAR archive", b"Rar!\x1a\x07\x00"),
    _MagicSignature("RAR archive", b"Rar!\x1a\x07\x01\x00"),
    _MagicSignature("7z archive", b"7z\xbc\xaf\x27\x1c"),
    _MagicSignature("gzip archive", b"\x1f\x8b"),
    _MagicSignature("bzip2 archive", b"BZh"),
    _MagicSignature("XZ archive", b"\xfd7zXZ\x00"),
    _MagicSignature("Zstandard archive", b"\x28\xb5\x2f\xfd"),
    _MagicSignature("PDF document", b"%PDF-"),
    _MagicSignature("Windows cabinet archive", b"MSCF"),
    _MagicSignature("ISO disk image", b"CD001"),
    _MagicSignature("PNG image", b"\x89PNG\r\n\x1a\n"),
    _MagicSignature("JPEG image", b"\xff\xd8\xff"),
    _MagicSignature("GIF image", b"GIF87a"),
    _MagicSignature("GIF image", b"GIF89a"),
    _MagicSignature("BMP image", b"BM"),
    _MagicSignature("Windows icon/cursor image", b"\x00\x00\x01\x00"),
    _MagicSignature("Windows icon/cursor image", b"\x00\x00\x02\x00"),
    _MagicSignature("RIFF media payload", b"RIFF"),
    _MagicSignature("MP3 audio", b"ID3"),
    _MagicSignature("WOFF font", b"wOFF"),
    _MagicSignature("WOFF2 font", b"wOF2"),
    _MagicSignature("OpenType font", b"OTTO"),
    _MagicSignature("TrueType font", b"\x00\x01\x00\x00"),
    _MagicSignature("MP4 media", b"ftyp", offset=4),
)

_UTF8_BOM = b"\xef\xbb\xbf"
_SVG_PREFIX_RE = re.compile(
    rb"^(?:<\?xml[^>]*>\s*)?<svg(?:[\s>/]|$)",
    re.IGNORECASE,
)
_ALLOWED_TEXT_CONTROL_BYTES = {9, 10, 12, 13}


def content_type_base(content_type: str | None) -> str:
    """Return the lowercase media type without parameters."""
    if not content_type:
        return ""
    return content_type.lower().split(";", 1)[0].strip()


def is_allowed_document_content_type(content_type: str | None) -> bool:
    """Whether a Content-Type is compatible with docpull's document pipeline."""
    base_type = content_type_base(content_type)
    return not base_type or base_type in ALLOWED_DOCUMENT_CONTENT_TYPES


def _header_get(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _filename_from_content_disposition(value: str) -> str | None:
    message = Message()
    message["Content-Disposition"] = value
    filename = message.get_filename()
    return unquote(filename) if filename else None


def _repeated_unquote(value: str, *, limit: int = 5) -> str:
    """Decode percent-encoding repeatedly to catch nested extension tricks."""
    current = value
    for _ in range(limit):
        decoded = unquote(current)
        if decoded == current:
            return current
        current = decoded
    return current


def _path_extensions(value: str) -> list[str]:
    """Return all lowercase suffixes from a repeatedly decoded path/name."""
    decoded = _repeated_unquote(value)
    return [suffix.lower() for suffix in PurePosixPath(decoded).suffixes]


def _first_dangerous_extension(value: str) -> str | None:
    for extension in _path_extensions(value):
        if extension in _DANGEROUS_FILE_EXTENSIONS:
            return extension
    return None


def _textual_prefix(body_prefix: bytes) -> bytes:
    sample = body_prefix
    if sample.startswith(_UTF8_BOM):
        sample = sample[len(_UTF8_BOM) :]
    return sample.lstrip()


def _looks_like_svg_document(body_prefix: bytes) -> bool:
    return _SVG_PREFIX_RE.match(_textual_prefix(body_prefix[:1024])) is not None


def _looks_like_binary_text(body_prefix: bytes) -> bool:
    if not body_prefix:
        return False
    if b"\x00" in body_prefix:
        return True
    sample = body_prefix[:1024]
    control_count = sum(1 for byte in sample if byte < 32 and byte not in _ALLOWED_TEXT_CONTROL_BYTES)
    return len(sample) >= 64 and (control_count / len(sample)) > 0.05


class SafeDownloadPolicy:
    """Reject responses that look like downloadable files instead of readable web content."""

    max_sniff_bytes = 8192

    def __init__(self, *, allowed_remote_document_types: set[str] | None = None) -> None:
        self.allowed_remote_document_types = frozenset(
            content_type_base(value) for value in (allowed_remote_document_types or set())
        )

    @property
    def allows_pdf(self) -> bool:
        return "application/pdf" in self.allowed_remote_document_types

    def validate_request_url(self, url: str) -> None:
        """Fail before the request for URLs that clearly target unsafe files."""
        parsed = urlparse(url)
        extension = _first_dangerous_extension(parsed.path)
        if extension == ".pdf" and self.allows_pdf:
            extension = None
        if extension is not None:
            raise UnsafeDownloadError(
                f"Disallowed download URL extension '{extension}' for {url}. "
                "docpull only fetches readable web/text responses."
            )

    def validate_response_headers(
        self,
        url: str,
        *,
        status_code: int,
        headers: dict[str, str],
        content_type: str | None,
    ) -> None:
        """Fail before reading the body when headers identify a file download."""
        if status_code == 304 or status_code >= 400:
            return

        disposition = _header_get(headers, "Content-Disposition")
        if disposition:
            lowered = disposition.lower()
            filename = _filename_from_content_disposition(disposition)
            if "attachment" in lowered:
                attachment_type = content_type_base(content_type or _header_get(headers, "Content-Type"))
                if attachment_type not in self.allowed_remote_document_types:
                    raise UnsafeDownloadError(
                        f"Refusing attachment response from {url}; "
                        "docpull only fetches inline web/text content unless an explicit "
                        "remote-document type is enabled."
                    )
            if filename:
                extension = _first_dangerous_extension(filename)
                if extension == ".pdf" and self.allows_pdf:
                    extension = None
                if extension is not None:
                    raise UnsafeDownloadError(
                        f"Refusing response from {url}; Content-Disposition filename "
                        f"uses disallowed extension '{extension}'."
                    )

        base_type = content_type_base(content_type or _header_get(headers, "Content-Type"))
        if base_type in self.allowed_remote_document_types:
            return
        if base_type in _DANGEROUS_CONTENT_TYPES:
            raise UnsafeDownloadError(f"Disallowed content type '{base_type}' for {url}.")
        if base_type and base_type not in ALLOWED_DOCUMENT_CONTENT_TYPES:
            raise UnsafeDownloadError(
                f"Unsupported content type '{base_type}' for {url}; "
                "docpull only fetches readable web/text responses."
            )

    def validate_body_prefix(self, url: str, body_prefix: bytes) -> None:
        """Reject spoofed text responses once the first bytes reveal a file."""
        if not body_prefix:
            return

        for signature in _DANGEROUS_MAGIC_SIGNATURES:
            end = signature.offset + len(signature.prefix)
            if len(body_prefix) >= end and body_prefix[signature.offset : end] == signature.prefix:
                if signature.name == "PDF document" and self.allows_pdf:
                    return
                raise UnsafeDownloadError(f"Disallowed {signature.name} body while fetching {url}.")

        if _looks_like_svg_document(body_prefix):
            raise UnsafeDownloadError(f"Disallowed SVG document body while fetching {url}.")

        if _looks_like_binary_text(body_prefix):
            raise UnsafeDownloadError(f"Disallowed binary-looking body while fetching {url}.")

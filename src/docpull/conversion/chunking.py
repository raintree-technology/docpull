"""Token-aware Markdown chunking for LLM / RAG pipelines.

Splits long Markdown into chunks sized to a target token count, preferring to
break on heading boundaries and then paragraph boundaries. Falls back to a
conservative character-based token estimate when ``tiktoken`` is unavailable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Chunk:
    """A single chunk of Markdown.

    Attributes:
        index: 0-based position within the document.
        text: The chunk Markdown text.
        token_count: Number of tokens (exact if tiktoken is available).
        heading: Nearest preceding heading, if any.
    """

    index: int
    text: str
    token_count: int
    heading: str | None = None


class TokenCounter:
    """Count tokens with ``tiktoken`` when available, else estimate.

    The fallback estimate is deliberately conservative (one token per 4 chars)
    to avoid producing chunks that exceed model limits.
    """

    def __init__(self, encoding: str = "cl100k_base") -> None:
        self._encoding_name = encoding
        self._encoder = None
        try:
            import tiktoken

            self._encoder = tiktoken.get_encoding(encoding)
        except ImportError:
            logger.debug("tiktoken not installed; using character-based estimate")
        except Exception as err:  # noqa: BLE001
            logger.debug("tiktoken init failed (%s); using estimate", err)

    def count(self, text: str) -> int:
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        # Conservative 1 token ≈ 4 chars estimate.
        return max(1, len(text) // 4)

    @property
    def exact(self) -> bool:
        """Whether counts come from tiktoken (True) or the fallback."""
        return self._encoder is not None

    @property
    def encoding(self) -> str:
        return self._encoding_name


def _strip_frontmatter(markdown: str) -> tuple[str, str]:
    """Split YAML frontmatter from body."""
    if not markdown.startswith("---"):
        return "", markdown
    end = markdown.find("\n---", 3)
    if end == -1:
        return "", markdown
    fm_end = markdown.find("\n", end + 1)
    if fm_end == -1:
        return markdown[: end + 4], ""
    return markdown[: fm_end + 1], markdown[fm_end + 1 :]


def _split_on_headings(body: str) -> list[tuple[str | None, str]]:
    """Split Markdown into (heading, section) pairs.

    The first tuple may have ``heading=None`` for any preamble before the
    first heading.
    """
    matches: list[tuple[int, str]] = []
    in_fence = False
    fence_marker = ""
    offset = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            offset += len(line)
            continue
        if not in_fence:
            match = _HEADING_RE.match(line.rstrip("\n"))
            if match:
                matches.append((offset, match.group(2).strip()))
        offset += len(line)

    if not matches:
        return [(None, body)]

    sections: list[tuple[str | None, str]] = []
    if matches[0][0] > 0:
        sections.append((None, body[: matches[0][0]]))

    for i, (start, heading_line) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(body)
        chunk = body[start:end]
        sections.append((heading_line, chunk))
    return sections


def _split_paragraphs(section: str) -> list[str]:
    # Split on blank lines while preserving code blocks intact.
    parts: list[str] = []
    in_code = False
    fence_marker = ""
    buf: list[str] = []
    for line in section.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_code:
                in_code = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_code = False
                fence_marker = ""
        if line.strip() == "" and not in_code and buf:
            parts.append("\n".join(buf))
            buf = []
        else:
            buf.append(line)
    if buf:
        parts.append("\n".join(buf))
    return parts


def chunk_markdown(
    markdown: str,
    max_tokens: int = 4000,
    counter: TokenCounter | None = None,
    *,
    keep_frontmatter_in_first: bool = True,
) -> list[Chunk]:
    """Split Markdown into token-bounded chunks.

    Args:
        markdown: The full Markdown document (including any frontmatter).
        max_tokens: Soft upper bound per chunk. Oversized paragraphs may
            exceed this if they cannot be split further without losing
            structure.
        counter: Reusable token counter. One is created if omitted.
        keep_frontmatter_in_first: Prepend YAML frontmatter to chunk #0 so the
            first chunk is self-describing.

    Returns:
        Ordered list of ``Chunk`` objects. Always at least one chunk unless
        the input is empty.
    """
    counter = counter or TokenCounter()
    frontmatter, body = _strip_frontmatter(markdown)
    body = body.strip()
    if not body:
        return []

    sections = _split_on_headings(body)
    chunks: list[Chunk] = []
    buf_parts: list[str] = []
    buf_tokens = 0
    buf_heading: str | None = None

    def flush() -> None:
        nonlocal buf_parts, buf_tokens, buf_heading
        if not buf_parts:
            return
        text = "\n\n".join(part.strip() for part in buf_parts if part.strip())
        if not text:
            buf_parts = []
            buf_tokens = 0
            buf_heading = None
            return
        prefix = frontmatter if keep_frontmatter_in_first and not chunks else ""
        final = (prefix + text).strip() + "\n"
        chunks.append(
            Chunk(
                index=len(chunks),
                text=final,
                token_count=counter.count(final),
                heading=buf_heading,
            )
        )
        buf_parts = []
        buf_tokens = 0
        buf_heading = None

    def append_part(part: str, token_count: int, heading: str | None) -> None:
        nonlocal buf_tokens, buf_heading
        if not part.strip():
            return
        if buf_heading is None and heading is not None:
            buf_heading = heading
        buf_parts.append(part)
        buf_tokens += token_count

    for heading, section in sections:
        section_tokens = counter.count(section)
        if section_tokens <= max_tokens and buf_tokens + section_tokens <= max_tokens:
            append_part(section, section_tokens, heading)
            continue
        # Section alone fits but buffer is full: flush then add.
        if section_tokens <= max_tokens:
            flush()
            append_part(section, section_tokens, heading)
            continue
        # Section too large: flush current buffer, then split paragraphs.
        flush()
        for para in _split_paragraphs(section):
            p_tokens = counter.count(para)
            if p_tokens > max_tokens:
                # Hard case: single paragraph exceeds budget. Emit as its own
                # oversize chunk rather than truncating.
                flush()
                prefix = frontmatter if keep_frontmatter_in_first and not chunks else ""
                text = (prefix + para).strip() + "\n"
                chunks.append(
                    Chunk(
                        index=len(chunks),
                        text=text,
                        token_count=counter.count(text),
                        heading=heading,
                    )
                )
                continue
            if buf_tokens + p_tokens > max_tokens:
                flush()
            append_part(para, p_tokens, heading)

    flush()
    return chunks


__all__ = ["Chunk", "TokenCounter", "chunk_markdown"]

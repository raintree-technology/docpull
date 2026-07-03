"""Markdown cleanup for article-like pages after main-content extraction."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_TRUNCATE_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+)?("
    r"related topics|more on this story|more from .*|related stories|related content|"
    r"recommended stories|recommended|around the bbc|elsewhere on the bbc|"
    r"most read|top stories|read more"
    r")\s*$",
    re.IGNORECASE,
)
_IMAGE_SOURCE_RE = re.compile(r"\s*Image source,\s*.*$", re.IGNORECASE)
_IMAGE_CAPTION_RE = re.compile(r"\s*Image caption,\s*.*$", re.IGNORECASE)
_FIGURE_CAPTION_RE = re.compile(r"^(?:Figure|Image|Media)\s+caption,\s*$", re.IGNORECASE)
_BOILERPLATE_LINE_RE = re.compile(
    r"^(?:Share|Save|Listen to article|Open comments|Advertisement|Skip to content|Published)\s*$",
    re.IGNORECASE,
)
_TOP_STORY_META_RE = re.compile(
    r"^(?:"
    r"By\s?\S.*(?:BBC|Reuters|Associated Press|AP News|CNN|NPR|Guardian|Times|News|Mundo).*|"
    r"Watch:\s+.*|Updated\s+.*|Published\s+.*|"
    r"\d{1,2}\s+[A-Z][a-z]+\s+20\d{2},?\s+\d{1,2}:\d{2}\s+[A-Z]{2,4}"
    r")$"
)
_TOP_BYLINE_RE = re.compile(r"^By\s+[A-Z][A-Za-z .,'-]{2,80}$")
_TOP_NEWSROOM_RE = re.compile(
    r"^(?:"
    r"BBC(?:\s+[A-Z][A-Za-z]+)*\s+News|"
    r"Reuters|Associated Press|AP News|CNN|NPR|The Guardian|The New York Times|"
    r"Washington Post|NBC News|CBS News|ABC News"
    r")$"
)
_VIDEO_NOISE_RE = re.compile(
    r"^(?:This video can not be played|This video cannot be played|Media caption,)\b",
    re.IGNORECASE,
)
_NEWS_HOST_RE = re.compile(
    r"(^|\.)("
    r"bbc\.co\.uk|bbc\.com|apnews\.com|reuters\.com|theguardian\.com|nytimes\.com|"
    r"washingtonpost\.com|npr\.org|cnn\.com|abcnews\.go\.com|cbsnews\.com|nbcnews\.com"
    r")$",
    re.IGNORECASE,
)


def clean_article_markdown(markdown: str, *, url: str, metadata: dict[str, Any]) -> str:
    """Trim common article/news boilerplate while leaving docs pages alone."""
    if not _looks_article_like(url, metadata):
        return markdown

    lines = markdown.splitlines()
    cleaned: list[str] = []
    previous_blank = False
    pending_image_caption = False
    pending_label_caption = False
    for index, raw_line in enumerate(lines):
        raw_stripped = raw_line.strip()
        line = _IMAGE_SOURCE_RE.sub("", raw_line).rstrip()
        line = _IMAGE_CAPTION_RE.sub("", line).rstrip()
        stripped = line.strip()
        if _TRUNCATE_HEADING_RE.match(stripped):
            break
        if _FIGURE_CAPTION_RE.match(raw_stripped) or _FIGURE_CAPTION_RE.match(stripped):
            pending_label_caption = True
            continue
        if pending_label_caption and stripped:
            pending_label_caption = False
            if _looks_like_orphan_caption(stripped) or (index < 30 and _TOP_STORY_META_RE.match(stripped)):
                continue
        if (
            _BOILERPLATE_LINE_RE.match(stripped.lstrip("* "))
            or (index < 30 and _TOP_STORY_META_RE.match(stripped))
            or (index < 30 and _TOP_BYLINE_RE.match(stripped))
            or (index < 30 and _TOP_NEWSROOM_RE.match(stripped))
            or (index < 60 and _VIDEO_NOISE_RE.match(stripped))
        ):
            continue
        if pending_image_caption and stripped and _looks_like_orphan_caption(stripped):
            pending_image_caption = False
            continue
        if not stripped:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line)
        pending_image_caption = stripped.startswith("![")
        previous_blank = False

    result = "\n".join(cleaned).strip()
    return result + "\n" if result else markdown


def _looks_like_orphan_caption(line: str) -> bool:
    if len(line) > 180:
        return False
    if line.endswith((".", "?", "!")):
        return False
    return not line.startswith(("[", "http://", "https://", "##", "#"))


def _looks_article_like(url: str, metadata: dict[str, Any]) -> bool:
    if any(metadata.get(key) for key in ("published_time", "modified_time", "author", "section")):
        return True
    host = urlparse(url).netloc.lower()
    return bool(_NEWS_HOST_RE.search(host))


__all__ = ["clean_article_markdown"]

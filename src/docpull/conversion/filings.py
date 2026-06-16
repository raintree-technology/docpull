"""SEC filing-oriented HTML cleanup helpers."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

_HIDDEN_INLINE_XBRL_TAGS = {"ix:hidden", "ix:header", "ix:references", "ix:resources"}


def clean_inline_xbrl_html(html: bytes) -> tuple[bytes, dict[str, int]]:
    """Remove hidden Inline XBRL scaffolding while preserving visible facts.

    SEC filings often include machine-readable Inline XBRL tags around visible
    text plus hidden header/resources blocks. The visible ``ix:*`` tags are
    unwrapped so their text remains available, while hidden sections and
    browser-hidden elements are removed before extraction.
    """
    text = html.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    stats = {
        "hidden_inline_xbrl_removed": 0,
        "hidden_elements_removed": 0,
        "inline_xbrl_tags_unwrapped": 0,
    }

    for tag in list(soup.find_all(True)):
        if not isinstance(tag, Tag):
            continue
        name = (tag.name or "").lower()
        if not name:
            continue
        if name in _HIDDEN_INLINE_XBRL_TAGS:
            tag.decompose()
            stats["hidden_inline_xbrl_removed"] += 1
            continue
        if _is_hidden_element(tag):
            tag.decompose()
            stats["hidden_elements_removed"] += 1
            continue
        if name.startswith("ix:"):
            tag.unwrap()
            stats["inline_xbrl_tags_unwrapped"] += 1

    return str(soup).encode("utf-8"), stats


def _is_hidden_element(tag: Tag) -> bool:
    if tag.has_attr("hidden") or tag.get("aria-hidden") == "true":
        return True
    style = str(tag.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    classes = tag.get("class")
    return isinstance(classes, list) and any(str(item).lower() == "hidden" for item in classes)


__all__ = ["clean_inline_xbrl_html"]

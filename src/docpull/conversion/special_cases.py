"""Framework-specific fast extractors for common documentation platforms.

Many "SPAs" ship all their content as JSON inside the initial HTML response
(Next.js `__NEXT_DATA__`, Docusaurus static builds, Mintlify). Detecting and
parsing these feeds skips the JS render entirely and yields content that is
often cleaner than what html2text would produce from rendered HTML.

Each extractor is a best-effort heuristic: it returns Markdown on a match and
``None`` otherwise, letting the caller fall back to the generic extractor.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


@dataclass
class SpecialCaseResult:
    """Result from a special-case extractor.

    Attributes:
        markdown: The converted Markdown content.
        title: Extracted page title, if any.
        source_type: Short identifier of the extractor (e.g. ``"next_data"``).
        extra: Arbitrary extra metadata to surface in frontmatter.
    """

    markdown: str
    title: str | None = None
    source_type: str = "generic"
    extra: dict[str, Any] | None = None


class SpecialCaseExtractor(Protocol):
    """Protocol for framework-specific fast extractors.

    Extractors should be cheap to run and fail fast (return ``None``) when the
    input does not match their target framework.
    """

    name: str

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        """Attempt extraction; return ``None`` if not applicable."""
        ...


def _decode_html(html: bytes) -> str:
    """Decode HTML bytes with a conservative fallback chain."""
    try:
        return html.decode("utf-8")
    except UnicodeDecodeError:
        return html.decode("utf-8", errors="replace")


def _soup(html: bytes) -> BeautifulSoup:
    return BeautifulSoup(_decode_html(html), "html.parser")


def _walk_text(node: Any) -> str:
    """Recursively flatten a Next.js/MDX AST-like JSON tree to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_walk_text(c) for c in node)
    if isinstance(node, dict):
        for key in ("content", "children", "value", "text", "body"):
            if key in node:
                return _walk_text(node[key])
    return ""


class NextDataExtractor:
    """Extract content from Next.js ``__NEXT_DATA__`` JSON blobs.

    Covers a huge portion of modern doc sites (Vercel, Supabase, many Mintlify
    variants) without needing to render JS.
    """

    name = "next_data"

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        if b"__NEXT_DATA__" not in html:
            return None

        soup = _soup(html)
        tag = soup.find("script", id="__NEXT_DATA__")
        if not isinstance(tag, Tag):
            return None

        raw = tag.string or tag.get_text()
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as err:
            logger.debug("Failed to parse __NEXT_DATA__ for %s: %s", url, err)
            return None

        body = self._extract_body(data)
        title = self._extract_title(data)
        if not body or len(body.strip()) < 50:
            return None

        md = body
        if title:
            md = f"# {title}\n\n{md}"
        return SpecialCaseResult(
            markdown=md.strip() + "\n",
            title=title,
            source_type=self.name,
            extra={"framework": "nextjs"},
        )

    @staticmethod
    def _extract_title(data: dict[str, Any]) -> str | None:
        props = data.get("props", {}) or {}
        page_props = props.get("pageProps", {}) or {}
        for key in ("title", "pageTitle", "frontMatter"):
            value = page_props.get(key)
            if isinstance(value, dict):
                title = value.get("title")
                if isinstance(title, str):
                    return title
            elif isinstance(value, str):
                return value
        return None

    @staticmethod
    def _extract_body(data: dict[str, Any]) -> str:
        props = data.get("props", {}) or {}
        page_props = props.get("pageProps", {}) or {}
        candidates: list[Any] = [
            page_props.get("source"),
            page_props.get("mdxSource"),
            page_props.get("content"),
            page_props.get("markdownContent"),
            page_props.get("body"),
            page_props.get("page"),
        ]
        for cand in candidates:
            if cand is None:
                continue
            if isinstance(cand, str) and len(cand) > 100:
                return cand
            if isinstance(cand, dict):
                for key in ("compiledSource", "raw", "content", "body"):
                    val = cand.get(key)
                    if isinstance(val, str) and len(val) > 100:
                        return val
                flat = _walk_text(cand)
                if flat and len(flat) > 100:
                    return flat
        # Last ditch: flatten any large string-valued leaf.
        flat = _walk_text(page_props)
        if flat and len(flat) > 200:
            return flat
        return ""


class DocusaurusExtractor:
    """Detect Docusaurus pages and fall through to generic extraction.

    Docusaurus v2+ pages render full content into ``article`` tags at build
    time, so the generic extractor handles them fine. This extractor exists
    primarily to *tag* the source so downstream code knows the content is
    Docusaurus-shaped (for chunking, etc.).
    """

    name = "docusaurus"

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        # Signature: docusaurus writes a root div id and meta generator
        if b"docusaurus" not in html.lower() and b"__docusaurus" not in html:
            return None
        # Let the generic extractor handle it; we just flag the source.
        return None


class MintlifyExtractor:
    """Extract Mintlify doc pages via their ``_next/data`` JSON feed."""

    name = "mintlify"

    _MARKER = b"mintlify"

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        # Mintlify ships a meta generator and usually __NEXT_DATA__.
        # Prefer NextDataExtractor; this is a targeted fallback.
        if self._MARKER not in html.lower():
            return None
        # Delegate to Next data extractor logic with Mintlify tagging.
        result = NextDataExtractor().try_extract(html, url)
        if result is None:
            return None
        return SpecialCaseResult(
            markdown=result.markdown,
            title=result.title,
            source_type=self.name,
            extra={"framework": "mintlify"},
        )


class OpenApiExtractor:
    """Render OpenAPI / Swagger JSON specs directly to Markdown.

    Triggers only when the *content type* is JSON and the body parses as an
    OpenAPI document. A docpull crawl that stumbles on ``openapi.json`` gets a
    usable Markdown file instead of an unreadable blob.
    """

    name = "openapi"

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        text = _decode_html(html).lstrip()
        if not text.startswith("{"):
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        version = data.get("openapi") or data.get("swagger")
        if not isinstance(version, str):
            return None

        info = data.get("info", {}) or {}
        title = info.get("title") or "API Reference"
        description = info.get("description") or ""

        lines = [f"# {title}", ""]
        if version:
            lines.append(f"_OpenAPI {version}_")
            lines.append("")
        if description:
            lines.append(description.strip())
            lines.append("")

        paths = data.get("paths", {}) or {}
        for path, ops in sorted(paths.items()):
            if not isinstance(ops, dict):
                continue
            lines.append(f"## `{path}`")
            lines.append("")
            for method, op in ops.items():
                if method.startswith("x-") or not isinstance(op, dict):
                    continue
                summary = op.get("summary") or ""
                lines.append(f"### `{method.upper()} {path}` — {summary}".rstrip(" —"))
                lines.append("")
                op_desc = op.get("description")
                if op_desc:
                    lines.append(op_desc.strip())
                    lines.append("")
                params = op.get("parameters") or []
                if params:
                    lines.append("**Parameters:**")
                    lines.append("")
                    for param in params:
                        if not isinstance(param, dict):
                            continue
                        pname = param.get("name", "?")
                        pin = param.get("in", "?")
                        required = " (required)" if param.get("required") else ""
                        pdesc = param.get("description", "")
                        lines.append(f"- `{pname}` ({pin}){required} — {pdesc}".rstrip(" —"))
                    lines.append("")

        return SpecialCaseResult(
            markdown="\n".join(lines).strip() + "\n",
            title=str(title),
            source_type=self.name,
            extra={"framework": "openapi", "openapi_version": version},
        )


class SphinxObjectsInvExtractor:
    """Detect Sphinx-built docs and let the generic extractor run.

    Sphinx emits predictable ``div.body`` / ``div.document`` wrappers that
    ``MainContentExtractor`` already captures. We only tag the source so
    downstream code knows the content is Sphinx-flavored.
    """

    name = "sphinx"

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        if b'name="generator" content="Sphinx' not in html and b"sphinx" not in html.lower():
            return None
        return None


class MdxSourceExtractor:
    """Rewrite ``edit-this-page`` GitHub links to raw MDX source URLs.

    Many docs sites link to their source on GitHub (``/blob/...``). This
    extractor does NOT fetch the MDX itself — that happens in the pipeline
    step — but it exposes the raw URL so the fetch step can prefer it when
    ``prefer_source=True`` is set.
    """

    name = "mdx_source"

    _EDIT_PATTERNS = (
        re.compile(r'href="(https://github\.com/[^"]+/blob/[^"]+\.mdx?)"'),
        re.compile(r'href="(https://github\.com/[^"]+/edit/[^"]+\.mdx?)"'),
    )

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        # Informational only: set extra.source_mdx_url if found. The generic
        # extractor still runs. Content substitution happens in the pipeline
        # step if prefer_source is enabled.
        text = _decode_html(html[:200_000])  # scan first 200KB
        for pattern in self._EDIT_PATTERNS:
            match = pattern.search(text)
            if match:
                raw_url = (
                    match.group(1)
                    .replace("/blob/", "/raw/")
                    .replace("/edit/", "/raw/")
                )
                # Return None so downstream runs, but attach hint via a cache
                # mechanism. Simpler: return None always; step reads the URL
                # if needed by re-running the regex.
                logger.debug("Found MDX source link for %s: %s", url, raw_url)
                return None
        return None


# Default chain: order matters. Cheapest / most specific first.
DEFAULT_CHAIN: list[SpecialCaseExtractor] = [
    OpenApiExtractor(),
    MintlifyExtractor(),
    NextDataExtractor(),
    DocusaurusExtractor(),
    SphinxObjectsInvExtractor(),
    MdxSourceExtractor(),
]


def find_mdx_source_url(html: bytes) -> str | None:
    """Return a raw GitHub URL to the MDX source if the page links to one."""
    text = _decode_html(html[:200_000])
    for pattern in MdxSourceExtractor._EDIT_PATTERNS:
        match = pattern.search(text)
        if match:
            return (
                match.group(1).replace("/blob/", "/raw/").replace("/edit/", "/raw/")
            )
    return None


_LOADING_PATTERNS = re.compile(r"loading\.?\.?\.?|please wait|javascript required", re.IGNORECASE)


def looks_like_spa(html: bytes, min_body_ratio: float = 0.05) -> bool:
    """Heuristic: does this HTML appear to be a JS-only SPA?

    True when the non-script body text is very small relative to the overall
    page size and the page contains script tags. Not perfect, but good enough
    to warn an agent before it consumes empty Markdown.
    """
    if len(html) < 500:
        return False
    if b"<script" not in html.lower():
        return False
    try:
        soup = _soup(html)
    except Exception:  # noqa: BLE001
        return False
    # Remove scripts/styles before measuring.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body = soup.find("body")
    if body is None:
        return False
    text_len = len(body.get_text(strip=True))
    return text_len / max(len(html), 1) < min_body_ratio and text_len < 500


def looks_like_spa_output(markdown: str) -> bool:
    """Post-conversion heuristic: did the extractor return a SPA shell?

    Returns True when the entire Markdown body is dominated by "Loading..."
    placeholders or is too small to plausibly represent a doc page.
    """
    body = markdown.strip()
    if not body:
        return True
    if len(body) > 400:
        return False
    # Strip frontmatter first
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :].strip()
    # Count "Loading..." matches vs total words
    matches = _LOADING_PATTERNS.findall(body)
    words = len(body.split())
    if not words:
        return True
    return len(matches) >= 2 or (len(matches) >= 1 and words < 20)


_NEXTJS_APP_ROUTER_MARKERS = (
    b"__next_data__",
    b"/_next/static/",
    b"next-router-state-tree",
    b'"__n_e"',  # Next.js error marker
    b"self.__next_f.push",  # App Router RSC streaming flush
)


def detect_source_type(html: bytes, url: str) -> str:
    """Best-effort detection of the documentation framework behind a page."""
    lower = html.lower()
    if b"mintlify" in lower:
        return "mintlify"
    for marker in _NEXTJS_APP_ROUTER_MARKERS:
        if marker in lower:
            return "nextjs"
    if b"docusaurus" in lower:
        return "docusaurus"
    if b'name="generator" content="sphinx' in lower:
        return "sphinx"
    host = urlparse(url).hostname or ""
    if host.endswith("readthedocs.io"):
        return "sphinx"
    return "generic"


__all__ = [
    "DEFAULT_CHAIN",
    "DocusaurusExtractor",
    "MdxSourceExtractor",
    "MintlifyExtractor",
    "NextDataExtractor",
    "OpenApiExtractor",
    "SpecialCaseExtractor",
    "SpecialCaseResult",
    "SphinxObjectsInvExtractor",
    "detect_source_type",
    "find_mdx_source_url",
    "looks_like_spa",
    "looks_like_spa_output",
]

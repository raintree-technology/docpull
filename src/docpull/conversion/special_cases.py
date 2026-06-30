"""Framework-specific fast extractors for common documentation platforms.

Many "SPAs" ship all their content as JSON inside the initial HTML response
(Next.js `__NEXT_DATA__`, Docusaurus static builds, Mintlify). Detecting and
parsing these feeds skips the JS render entirely and yields content that is
often cleaner than what html2text would produce from rendered HTML.

Each extractor is a best-effort heuristic: it returns Markdown on a match and
``None`` otherwise, letting the caller fall back to the generic extractor.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import yaml
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


def _hostname_from_url(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _hostname_matches_domain(hostname: str, domain: str) -> bool:
    normalized_domain = domain.lower().rstrip(".")
    return hostname == normalized_domain or hostname.endswith(f".{normalized_domain}")


def _title_from_soup(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        if title:
            return title
    heading = soup.find(["h1", "h2"])
    if heading:
        title = heading.get_text(" ", strip=True)
        if title:
            return title
    return None


def _markdown_from_first_selector(
    html: bytes,
    url: str,
    selectors: tuple[str, ...],
) -> tuple[str, str | None] | None:
    """Convert the first useful static HTML region to Markdown."""
    soup = _soup(html)
    for selector in selectors:
        node = soup.select_one(selector)
        if not isinstance(node, Tag):
            continue
        text = node.get_text(" ", strip=True)
        if len(text) < 80:
            continue
        from .markdown import HtmlToMarkdown

        markdown = HtmlToMarkdown().convert(str(node), url).strip()
        if markdown:
            return markdown + "\n", _title_from_soup(soup)
    return None


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
                for key in ("raw", "content", "body", "markdown", "markdownContent"):
                    val = cand.get(key)
                    if isinstance(val, str) and len(val) > 100:
                        return val
                val = cand.get("compiledSource")
                if isinstance(val, str) and len(val) > 100 and not _looks_like_compiled_mdx(val):
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
    """Extract static Docusaurus documentation pages."""

    name = "docusaurus"

    _SELECTORS = (
        "article",
        "main article",
        ".theme-doc-markdown",
        ".markdown",
        "main",
    )

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        # Signature: docusaurus writes a root div id and meta generator
        if b"docusaurus" not in html.lower() and b"__docusaurus" not in html:
            return None
        extracted = _markdown_from_first_selector(html, url, self._SELECTORS)
        if extracted is None:
            return None
        markdown, title = extracted
        return SpecialCaseResult(
            markdown=markdown,
            title=title,
            source_type=self.name,
            extra={"framework": "docusaurus"},
        )


class StaticRegionFrameworkExtractor:
    """Base extractor for frameworks that ship useful static HTML regions."""

    name = "static_framework"
    framework = "static"
    markers: tuple[bytes, ...] = ()
    host_domains: tuple[str, ...] = ()
    selectors: tuple[str, ...] = ("article", "main")

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        lower = html.lower()
        has_marker = any(marker in lower for marker in self.markers)
        host = _hostname_from_url(url)
        has_matching_host = any(_hostname_matches_domain(host, domain) for domain in self.host_domains)
        if (self.markers or self.host_domains) and not (has_marker or has_matching_host):
            return None
        extracted = _markdown_from_first_selector(html, url, self.selectors)
        if extracted is None:
            return None
        markdown, title = extracted
        return SpecialCaseResult(
            markdown=markdown,
            title=title,
            source_type=self.name,
            extra={"framework": self.framework},
        )


class MkDocsMaterialExtractor(StaticRegionFrameworkExtractor):
    """Extract MkDocs / Material for MkDocs static pages."""

    name = "mkdocs"
    framework = "mkdocs"
    markers = (b"mkdocs", b"md-content", b"material for mkdocs")
    selectors = (
        "article.md-content__inner",
        ".md-content__inner",
        ".md-content",
        "main article",
        "article",
        "main",
    )


class VitePressExtractor(StaticRegionFrameworkExtractor):
    """Extract VitePress / VuePress-style static docs pages."""

    name = "vitepress"
    framework = "vitepress"
    markers = (b"vitepress", b"vp-doc", b"vpdoc")
    selectors = (
        ".VPDoc .content",
        ".vp-doc",
        "main .content",
        "main",
        "article",
    )


class StarlightExtractor(StaticRegionFrameworkExtractor):
    """Extract Astro Starlight static docs pages."""

    name = "starlight"
    framework = "starlight"
    markers = (b"starlight", b"sl-markdown-content", b"astro")
    selectors = (
        ".sl-markdown-content",
        "article",
        "main",
    )


class GitBookExtractor(StaticRegionFrameworkExtractor):
    """Extract GitBook static documentation pages."""

    name = "gitbook"
    framework = "gitbook"
    markers = (b"gitbook", b'data-testid="page.content"', b"data-testid='page.content'")
    selectors = (
        "[data-testid='page.content']",
        '[data-testid="page.content"]',
        ".markdown",
        "main article",
        "article",
        "main",
    )


class ReadMeExtractor(StaticRegionFrameworkExtractor):
    """Extract ReadMe.io static documentation pages."""

    name = "readme"
    framework = "readme"
    markers = (b"rm-markdown", b"rdmd")
    host_domains = ("readme.io",)
    selectors = (
        ".rm-Markdown",
        ".rm-markdown",
        ".markdown-body",
        "article",
        "main",
    )


class RedocScalarExtractor(StaticRegionFrameworkExtractor):
    """Extract static Redoc or Scalar API reference pages."""

    name = "api_reference"
    framework = "redoc_scalar"
    markers = (b"redoc", b"scalar-api-reference", b"scalar")
    selectors = (
        "redoc",
        "#redoc-container",
        ".api-content",
        ".scalar-app",
        "article",
        "main",
    )


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


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def _clean_text(value: Any) -> str:
    """Strip HTML tags, decode entities, and collapse whitespace."""
    if not isinstance(value, str) or not value:
        return ""
    stripped = _HTML_TAG_RE.sub("", value)
    unescaped = html_lib.unescape(stripped)
    return re.sub(r"\s+", " ", unescaped).strip()


def _split_markdown_frontmatter(markdown: str) -> tuple[str | None, str]:
    """Return (frontmatter, body) for a Markdown document with YAML frontmatter."""
    text = markdown.lstrip("\ufeff")
    if not text.startswith("---"):
        return None, markdown
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, markdown
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None, markdown


def _frontmatter_title(frontmatter: str | None) -> str | None:
    if not frontmatter:
        return None
    for line in frontmatter.splitlines():
        if not line.lower().startswith("title:"):
            continue
        title = line.split(":", 1)[1].strip().strip("\"'")
        return title[:120] if title else None
    return None


def _looks_like_compiled_mdx(value: str) -> bool:
    head = value[:500]
    return "function MDXContent" in head or "_jsx(" in head or "jsxDEV(" in head


def _resolve_ref(spec: dict[str, Any], ref: str) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _describe_type(schema: Any, spec: dict[str, Any]) -> str:
    """One-line type description for a schema, without recursing into properties."""
    if not isinstance(schema, dict):
        return "?"
    if "$ref" in schema:
        ref: str = schema["$ref"]
        return ref.rsplit("/", 1)[-1]
    for key in ("oneOf", "anyOf", "allOf"):
        if isinstance(schema.get(key), list) and schema[key]:
            seen: list[str] = []
            for sub in schema[key]:
                desc = _describe_type(sub, spec)
                if desc not in seen:
                    seen.append(desc)
            if key == "allOf":
                return f"({' & '.join(seen)})"
            return " | ".join(seen)
    t = schema.get("type")
    if t == "array":
        return f"array<{_describe_type(schema.get('items') or {}, spec)}>"
    if isinstance(t, list):
        return " | ".join(str(x) for x in t)
    fmt = schema.get("format")
    if isinstance(t, str):
        return f"{t}({fmt})" if fmt else t
    if "enum" in schema:
        return "enum"
    if isinstance(schema.get("properties"), dict):
        return "object"
    return "any"


def _schema_properties(
    schema: Any, spec: dict[str, Any], seen: frozenset[str] = frozenset()
) -> tuple[dict[str, Any], set[str]]:
    """Return ({name: subschema}, required_set) for a schema, resolving $ref and allOf.

    Does not recurse into nested objects — callers render one level.
    """
    if not isinstance(schema, dict):
        return {}, set()
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return {}, set()
        resolved = _resolve_ref(spec, ref)
        return _schema_properties(resolved, spec, seen | {ref})
    props: dict[str, Any] = {}
    required: set[str] = set()
    if isinstance(schema.get("allOf"), list):
        for sub in schema["allOf"]:
            sub_props, sub_required = _schema_properties(sub, spec, seen)
            props.update(sub_props)
            required.update(sub_required)
    direct = schema.get("properties")
    if isinstance(direct, dict):
        props.update(direct)
    req = schema.get("required")
    if isinstance(req, list):
        required.update(r for r in req if isinstance(r, str))
    return props, required


class OpenApiExtractor:
    """Render OpenAPI / Swagger JSON or YAML specs directly to Markdown.

    Triggers only when the body parses as an OpenAPI document. Renders each
    operation with description, parameters (grouped by location), request body
    properties, and response schemas — with ``$ref``s followed one level and
    HTML tags stripped from descriptions.
    """

    name = "openapi"

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        text = _decode_html(html).lstrip()
        data = self._parse_spec(text, url)
        if data is None:
            return None

        version = data.get("openapi") or data.get("swagger")
        if not isinstance(version, str):
            return None

        info = data.get("info", {}) or {}
        title = info.get("title") or "API Reference"
        description = _clean_text(info.get("description") or "")

        lines = [f"# {title}", ""]
        if version:
            lines.append(f"_OpenAPI {version}_")
            lines.append("")
        if description:
            lines.append(description)
            lines.append("")

        paths = data.get("paths", {}) or {}
        for path, ops in sorted(paths.items()):
            if not isinstance(ops, dict):
                continue
            lines.append(f"## `{path}`")
            lines.append("")
            shared_params = ops.get("parameters") or []
            for method, op in ops.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                    continue
                self._render_operation(lines, path, method, op, shared_params, data)

        return SpecialCaseResult(
            markdown="\n".join(lines).strip() + "\n",
            title=str(title),
            source_type=self.name,
            extra={"framework": "openapi", "openapi_version": version},
        )

    @staticmethod
    def _parse_spec(text: str, url: str) -> dict[str, Any] | None:
        if not text:
            return None
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as err:
                logger.debug("OpenAPI extractor skipped %s: JSON parse failed: %s", url, err)
                return None
            return data if isinstance(data, dict) else None
        if not OpenApiExtractor._looks_like_yaml_spec(text, url):
            return None
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as err:
            logger.debug("OpenAPI extractor skipped %s: YAML parse failed: %s", url, err)
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _looks_like_yaml_spec(text: str, url: str) -> bool:
        path = urlparse(url).path.lower()
        if path.endswith((".yaml", ".yml")):
            return True
        head = text[:4000]
        return re.search(r"(?m)^\s*(openapi|swagger)\s*:", head) is not None

    def _render_operation(
        self,
        lines: list[str],
        path: str,
        method: str,
        op: dict[str, Any],
        shared_params: list[Any],
        spec: dict[str, Any],
    ) -> None:
        summary = _clean_text(op.get("summary") or "")
        header = f"### `{method.upper()} {path}`"
        if summary:
            header = f"{header} — {summary}"
        lines.append(header)
        lines.append("")
        op_desc = _clean_text(op.get("description") or "")
        if op_desc:
            lines.append(op_desc)
            lines.append("")

        self._render_parameters(lines, list(shared_params) + list(op.get("parameters") or []))
        self._render_request_body(lines, op.get("requestBody"), spec)
        self._render_responses(lines, op.get("responses"), spec)

    def _render_parameters(self, lines: list[str], params: list[Any]) -> None:
        buckets: dict[str, list[tuple[str, str, bool, str]]] = {}
        for param in params:
            if not isinstance(param, dict):
                continue
            pin = param.get("in", "query")
            pname = param.get("name", "?")
            ptype = _describe_type(param.get("schema") or {}, {})
            required = bool(param.get("required")) or pin == "path"
            pdesc = _clean_text(param.get("description") or "")
            buckets.setdefault(pin, []).append((pname, ptype, required, pdesc))
        order = ["path", "query", "header", "cookie"]
        for pin in order + sorted(set(buckets) - set(order)):
            items = buckets.get(pin)
            if not items:
                continue
            lines.append(f"**{pin.title()} parameters:**")
            lines.append("")
            for pname, ptype, required, pdesc in items:
                req = " (required)" if required else ""
                bullet = f"- `{pname}` ({ptype}){req}"
                if pdesc:
                    bullet += f" — {pdesc}"
                lines.append(bullet)
            lines.append("")

    def _render_request_body(self, lines: list[str], body: Any, spec: dict[str, Any]) -> None:
        if not isinstance(body, dict):
            return
        if "$ref" in body:
            resolved = _resolve_ref(spec, body["$ref"])
            if isinstance(resolved, dict):
                body = resolved
            else:
                return
        content = body.get("content")
        if not isinstance(content, dict) or not content:
            return
        content_type, media = self._pick_content_type(content)
        schema = media.get("schema") if isinstance(media, dict) else None
        required_body = bool(body.get("required"))
        header = "**Request body"
        if content_type:
            header += f" (`{content_type}`)"
        if required_body:
            header += " — required"
        header += ":**"
        lines.append(header)
        lines.append("")
        body_desc = _clean_text(body.get("description") or "")
        if body_desc:
            lines.append(body_desc)
            lines.append("")
        props, required = _schema_properties(schema or {}, spec)
        if props:
            for name, sub in props.items():
                if not isinstance(sub, dict):
                    continue
                ptype = _describe_type(sub, spec)
                req = " (required)" if name in required else ""
                pdesc = _clean_text(sub.get("description") or "")
                bullet = f"- `{name}` ({ptype}){req}"
                if pdesc:
                    bullet += f" — {pdesc}"
                lines.append(bullet)
        elif isinstance(schema, dict):
            lines.append(f"- body: {_describe_type(schema, spec)}")
        lines.append("")

    def _render_responses(self, lines: list[str], responses: Any, spec: dict[str, Any]) -> None:
        if not isinstance(responses, dict) or not responses:
            return
        lines.append("**Responses:**")
        lines.append("")
        for code, resp in sorted(responses.items(), key=lambda kv: str(kv[0])):
            if not isinstance(resp, dict):
                continue
            if "$ref" in resp:
                resolved = _resolve_ref(spec, resp["$ref"])
                if isinstance(resolved, dict):
                    resp = resolved
                else:
                    continue
            desc = _clean_text(resp.get("description") or "")
            content = resp.get("content")
            type_hint = ""
            if isinstance(content, dict) and content:
                _, media = self._pick_content_type(content)
                schema = media.get("schema") if isinstance(media, dict) else None
                if isinstance(schema, dict):
                    type_hint = f" → `{_describe_type(schema, spec)}`"
            bullet = f"- `{code}`{type_hint}"
            if desc:
                bullet += f" — {desc}"
            lines.append(bullet)
        lines.append("")

    @staticmethod
    def _pick_content_type(content: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        for preferred in ("application/json", "application/x-www-form-urlencoded", "multipart/form-data"):
            if preferred in content and isinstance(content[preferred], dict):
                return preferred, content[preferred]
        key = next(iter(content))
        value = content[key]
        return key, value if isinstance(value, dict) else {}


class RawTextExtractor:
    """Render raw Markdown and documentation text responses directly.

    The fetch step allows `text/plain` and Markdown content types so agent-facing
    indexes such as `llms.txt` can be ingested. BeautifulSoup cannot extract
    useful content from a bare text response, so this extractor handles
    Markdown-like URLs before the generic HTML path runs.
    """

    name = "raw_text"

    _TEXT_SUFFIXES = (".md", ".markdown", ".mdx", ".txt")
    _HTML_MARKERS = ("<html", "<body", "<!doctype")

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if not path.endswith(self._TEXT_SUFFIXES):
            return None

        text = _decode_html(html).strip()
        if not text:
            return None
        lower_head = text[:500].lower()
        if any(marker in lower_head for marker in self._HTML_MARKERS):
            return None
        if path.endswith(".txt") and not self._looks_like_docs_text(text, path):
            return None

        title = self._extract_title(text) or parsed.path.rsplit("/", 1)[-1] or "Document"
        source_type = "llms_txt" if path.endswith(("/llms.txt", "/llms-full.txt")) else self.name
        return SpecialCaseResult(
            markdown=text.rstrip() + "\n",
            title=title,
            source_type=source_type,
            extra={"framework": source_type},
        )

    @staticmethod
    def _looks_like_docs_text(text: str, path: str) -> bool:
        if path.endswith(("/llms.txt", "/llms-full.txt")):
            return True
        lines = [line.strip() for line in text.splitlines()[:20] if line.strip()]
        if not lines:
            return False
        markdown_signals = sum(
            1 for line in lines if line.startswith(("#", "-", "*", ">", "```")) or _MD_LINK_RE.search(line)
        )
        return markdown_signals >= 2

    @staticmethod
    def _extract_title(text: str) -> str | None:
        frontmatter, body = _split_markdown_frontmatter(text)
        title = _frontmatter_title(frontmatter)
        if title:
            return title
        text = body
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                if title:
                    return title[:120]
            return stripped[:120]
        return None


class SphinxObjectsInvExtractor:
    """Extract static Sphinx-built documentation pages."""

    name = "sphinx"

    _SELECTORS = (
        "div.body",
        "main div.body",
        "div.document",
        "[role='main']",
        "main",
        "article",
    )

    def try_extract(self, html: bytes, url: str) -> SpecialCaseResult | None:
        host = _hostname_from_url(url)
        if (
            b'name="generator" content="sphinx' not in html.lower()
            and b"sphinx" not in html.lower()
            and not _hostname_matches_domain(host, "readthedocs.io")
        ):
            return None
        extracted = _markdown_from_first_selector(html, url, self._SELECTORS)
        if extracted is None:
            return None
        markdown, title = extracted
        return SpecialCaseResult(
            markdown=markdown,
            title=title,
            source_type=self.name,
            extra={"framework": "sphinx"},
        )


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
                raw_url = match.group(1).replace("/blob/", "/raw/").replace("/edit/", "/raw/")
                # Return None so downstream runs, but attach hint via a cache
                # mechanism. Simpler: return None always; step reads the URL
                # if needed by re-running the regex.
                logger.debug("Found MDX source link for %s: %s", url, raw_url)
                return None
        return None


# Default chain: order matters. Cheapest / most specific first.
# MdxSourceExtractor is intentionally absent — it always returns None today
# and is exposed via `find_mdx_source_url` for callers that want to wire it
# manually (e.g. a `prefer_source` pipeline step).
DEFAULT_CHAIN: list[SpecialCaseExtractor] = [
    OpenApiExtractor(),
    RawTextExtractor(),
    MintlifyExtractor(),
    NextDataExtractor(),
    MkDocsMaterialExtractor(),
    VitePressExtractor(),
    StarlightExtractor(),
    GitBookExtractor(),
    ReadMeExtractor(),
    RedocScalarExtractor(),
    DocusaurusExtractor(),
    SphinxObjectsInvExtractor(),
]


def find_mdx_source_url(html: bytes) -> str | None:
    """Return a raw GitHub URL to the MDX source if the page links to one."""
    text = _decode_html(html[:200_000])
    for pattern in MdxSourceExtractor._EDIT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).replace("/blob/", "/raw/").replace("/edit/", "/raw/")
    return None


_LOADING_PATTERNS = re.compile(r"loading\.?\.?\.?|please wait|javascript required", re.IGNORECASE)


def looks_like_spa(html: bytes, min_body_ratio: float = 0.05) -> bool:
    """Heuristic: does this HTML appear to be a JS-only SPA?

    True when the non-script body text is very small relative to the overall
    page size and the page contains script tags. This is a conservative signal
    for warning an agent before it consumes empty Markdown.
    """
    if len(html) < 500:
        return False
    if b"<script" not in html.lower():
        return False
    try:
        soup = _soup(html)
    except Exception as err:  # noqa: BLE001
        logger.debug("SPA heuristic skipped malformed HTML: %s", err)
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
    if b"mkdocs" in lower or b"md-content" in lower:
        return "mkdocs"
    if b"vitepress" in lower or b"vp-doc" in lower:
        return "vitepress"
    if b"starlight" in lower or b"sl-markdown-content" in lower:
        return "starlight"
    if b"gitbook" in lower or b'data-testid="page.content"' in lower:
        return "gitbook"
    host = _hostname_from_url(url)
    if _hostname_matches_domain(host, "readme.io") or b"rm-markdown" in lower:
        return "readme"
    if b"redoc" in lower or b"scalar-api-reference" in lower:
        return "api_reference"
    if b"docusaurus" in lower:
        return "docusaurus"
    if b'name="generator" content="sphinx' in lower:
        return "sphinx"
    if _hostname_matches_domain(host, "readthedocs.io"):
        return "sphinx"
    return "generic"


__all__ = [
    "DEFAULT_CHAIN",
    "DocusaurusExtractor",
    "GitBookExtractor",
    "MkDocsMaterialExtractor",
    "MdxSourceExtractor",
    "MintlifyExtractor",
    "NextDataExtractor",
    "OpenApiExtractor",
    "RawTextExtractor",
    "ReadMeExtractor",
    "RedocScalarExtractor",
    "SpecialCaseExtractor",
    "SpecialCaseResult",
    "SphinxObjectsInvExtractor",
    "StarlightExtractor",
    "VitePressExtractor",
    "detect_source_type",
    "find_mdx_source_url",
    "looks_like_spa",
    "looks_like_spa_output",
]

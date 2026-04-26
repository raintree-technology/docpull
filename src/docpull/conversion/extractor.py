"""Main content extraction from HTML pages."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Elements that typically contain main content
CONTENT_SELECTORS = [
    "article",
    "main",
    '[role="main"]',
    ".content",
    ".main-content",
    ".post-content",
    ".article-content",
    ".documentation",
    ".docs-content",
    "#content",
    "#main-content",
    "#documentation",
]

# Elements to remove (navigation, ads, cookie banners, etc.)
REMOVE_SELECTORS = [
    "nav",
    "header",
    "footer",
    "aside",
    ".nav",
    ".navbar",
    ".sidebar",
    ".footer",
    ".header",
    ".menu",
    ".toc",
    ".table-of-contents",
    ".advertisement",
    ".ads",
    ".social-share",
    ".comments",
    ".related-posts",
    '[role="navigation"]',
    '[role="banner"]',
    '[role="contentinfo"]',
    '[aria-hidden="true"]',
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    # Cookie / consent / GDPR walls. Most are structural — class names
    # come from a small set of vendor SDKs (OneTrust, Osano, CookieConsent,
    # CookieLaw, Cookiebot, Iubenda) plus generic `.cookie-*` / `.gdpr-*`
    # patterns. The aria-label fallbacks catch dialogs whose className
    # doesn't match the conventions but whose accessibility label does.
    ".cookie-banner",
    ".cookie-consent",
    ".cookie-notice",
    ".cookielaw-banner",
    ".cookiebot",
    ".gdpr",
    ".gdpr-banner",
    ".consent-banner",
    ".consent-popup",
    ".cc-window",
    ".cc-banner",
    ".osano-cm-window",
    ".osano-cm-dialog",
    "#onetrust-banner-sdk",
    "#onetrust-consent-sdk",
    "#onetrust-pc-sdk",
    ".ot-sdk-container",
    ".iubenda-cs-container",
    ".termly-styl-banner",
    '[aria-label*="cookie" i]',
    '[aria-label*="consent" i]',
    '[aria-label*="gdpr" i]',
]

# Elements to preserve but simplify
PRESERVE_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "pre",
    "code",
    "blockquote",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "a",
    "strong",
    "em",
    "b",
    "i",
    "br",
    "hr",
    "img",
    "div",
    "span",  # Kept for structure
}


class MainContentExtractor:
    """
    Extracts main content from HTML documents.

    Uses heuristics to find the main content area and removes
    navigation, ads, and other non-content elements.

    Example:
        extractor = MainContentExtractor()
        content = extractor.extract(html_bytes, "https://docs.example.com/page")
    """

    def __init__(
        self,
        content_selectors: list[str] | None = None,
        remove_selectors: list[str] | None = None,
        preserve_images: bool = True,
        preserve_code_blocks: bool = True,
    ):
        """
        Initialize the content extractor.

        Args:
            content_selectors: CSS selectors for main content (overrides defaults)
            remove_selectors: CSS selectors for elements to remove (extends defaults)
            preserve_images: Whether to preserve img tags
            preserve_code_blocks: Whether to preserve pre/code formatting
        """
        self._content_selectors = content_selectors or CONTENT_SELECTORS
        self._remove_selectors = list(REMOVE_SELECTORS)
        if remove_selectors:
            self._remove_selectors.extend(remove_selectors)
        self._preserve_images = preserve_images
        self._preserve_code_blocks = preserve_code_blocks

    def _detect_encoding(self, html: bytes) -> str:
        """Detect character encoding from HTML content."""
        # Quick regex check for meta charset.
        head = html[:2048].decode("latin-1", errors="ignore")
        charset_match = re.search(r'charset=["\']?([^"\'\s>]+)', head, re.IGNORECASE)
        if charset_match:
            return charset_match.group(1).strip()
        return "utf-8"

    def _parse_html(self, html: bytes) -> BeautifulSoup:
        """Parse HTML bytes to BeautifulSoup."""
        encoding = self._detect_encoding(html)
        try:
            text = html.decode(encoding, errors="replace")
        except (UnicodeDecodeError, LookupError):
            text = html.decode("utf-8", errors="replace")
        return BeautifulSoup(text, "html.parser")

    def _find_main_content(self, soup: BeautifulSoup) -> Tag | None:
        """Find the main content element using selectors."""
        for selector in self._content_selectors:
            element = soup.select_one(selector)
            if element and len(element.get_text(strip=True)) > 100:
                return element

        # Fallback: find largest text block
        body = soup.find("body")
        if isinstance(body, Tag):
            return body

        # Last resort: return entire soup wrapped as Tag
        return None

    def _remove_unwanted(self, element: Tag) -> None:
        """Remove navigation, ads, and other unwanted elements."""
        for selector in self._remove_selectors:
            for el in element.select(selector):
                el.decompose()

    def _clean_attributes(self, element: Tag) -> None:
        """Remove unnecessary attributes from elements."""
        keep_attrs = {"href", "src", "alt", "title", "class", "id"}

        for tag in element.find_all(True):
            # Get list of attrs to remove (can't modify during iteration)
            attrs_to_remove = [attr for attr in tag.attrs if attr not in keep_attrs]
            for attr in attrs_to_remove:
                del tag[attr]

            # Remove empty class/id
            if tag.get("class") == []:
                del tag["class"]
            if tag.get("id") == "":
                del tag["id"]

    def _resolve_links(self, element: Tag, base_url: str) -> None:
        """Convert relative URLs to absolute URLs."""
        urlparse(base_url)

        # Resolve href attributes
        for tag in element.find_all("a", href=True):
            href = tag["href"]
            if href.startswith("#"):
                continue  # Keep anchor links
            if not href.startswith(("http://", "https://", "//")):
                tag["href"] = urljoin(base_url, href)

        # Resolve src attributes
        for tag in element.find_all(src=True):
            src = tag["src"]
            if not src.startswith(("http://", "https://", "//", "data:")):
                tag["src"] = urljoin(base_url, src)

    def _clean_whitespace(self, text: str) -> str:
        """Clean up excessive whitespace."""
        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Remove excessive blank lines (more than 2)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Remove trailing whitespace on lines
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        return text.strip()

    def extract(self, html: bytes, url: str) -> str:
        """
        Extract main content from HTML.

        Args:
            html: Raw HTML bytes
            url: Source URL for resolving relative links

        Returns:
            Cleaned HTML content as string
        """
        soup = self._parse_html(html)

        # Find main content
        main_content = self._find_main_content(soup)
        if main_content is None:
            # Try the entire document as fallback
            body_element = soup.find("body")
            if not isinstance(body_element, Tag):
                logger.warning(f"Could not find main content for {url}")
                return ""
            main_content = body_element

        # Make a copy to avoid modifying original
        content = BeautifulSoup(str(main_content), "html.parser")

        # Clean up
        self._remove_unwanted(content)
        # Normalize fence languages BEFORE we strip attributes — many
        # syntax-highlight conventions encode language in `class` (Prism:
        # `language-python`, highlight.js: `lang-py`, Shiki: `language-bash`).
        # html2text's `mark_code` won't pick these up by default, so we lift
        # the language onto an html2text-friendly `class="lang-X"` form on
        # both the <pre> and inner <code>.
        _normalize_code_fence_language(content)
        self._clean_attributes(content)
        self._resolve_links(content, url)

        result = str(content)
        return self._clean_whitespace(result)


# Map syntax-highlight library conventions to a canonical short language tag.
# Order matters: longest/most-specific prefix first so `highlight-source-rust`
# resolves to `rust`, not `source-rust`. We deliberately skip `none`, `text`,
# and `plaintext` — they represent "no language."
_LANG_CLASS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|\s)highlight-source-([\w+#-]+)", re.IGNORECASE),
    re.compile(r"(?:^|\s)hljs-language-([\w+#-]+)", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:language|lang|highlight)-([\w+#-]+)", re.IGNORECASE),
]

# Sentinel injected as the first text node inside a <code> tag. html2text
# preserves the body of <pre><code> verbatim (it just indents by 4 spaces
# and wraps in [code]/[/code]), so this sentinel survives through to the
# Markdown stage where HtmlToMarkdown._clean_output recovers the language
# and rewrites the block as a fenced GFM code block.
DOCPULL_FENCE_SENTINEL_PREFIX = "__DOCPULL_FENCE_LANG_"
DOCPULL_FENCE_SENTINEL_SUFFIX = "__"


def _classes_of(tag: Tag) -> list[str]:
    """Return a tag's CSS classes as a flat list of strings.

    BeautifulSoup hands back ``str``, ``AttributeValueList``, or ``None``
    depending on parser version. Normalize to ``list[str]`` for the rest
    of the language-detection code.
    """
    raw = tag.get("class")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(c) for c in raw]


def _detect_lang(class_string: str) -> str | None:
    """Return the canonical language tag for a code block, or None."""
    for pattern in _LANG_CLASS_PATTERNS:
        match = pattern.search(class_string)
        if not match:
            continue
        lang = match.group(1).lower()
        if lang in {"none", "plaintext", "text"}:
            return None
        return lang
    return None


def _normalize_code_fence_language(content: BeautifulSoup) -> None:
    """Inject a sentinel that lets the Markdown stage emit fenced blocks.

    Modern syntax-highlight libraries encode the language as a CSS class
    (Prism: ``language-python``; highlight.js: ``lang-py`` /
    ``hljs-language-bash``; GitHub: ``highlight-source-rust``). html2text
    cannot read these and emits a generic ``[code]...[/code]`` block.

    We walk every ``<pre>`` and prepend a sentinel ``__DOCPULL_FENCE_LANG_X__``
    as a NavigableString to the inner ``<code>`` (or to the ``<pre>`` itself
    if no inner ``<code>`` exists). Post-conversion, the Markdown layer
    pulls that sentinel back out of the rendered text and rewrites the
    block as a GFM fenced code block with the language tag.
    """
    for pre in content.find_all("pre"):
        if not isinstance(pre, Tag):
            continue
        pre_classes = _classes_of(pre)
        code = pre.find("code") if pre else None
        code_classes: list[str] = []
        if isinstance(code, Tag):
            code_classes = _classes_of(code)

        lang = _detect_lang(" ".join(pre_classes + code_classes))
        if lang is None:
            continue

        sentinel = f"{DOCPULL_FENCE_SENTINEL_PREFIX}{lang}{DOCPULL_FENCE_SENTINEL_SUFFIX}\n"
        target = code if isinstance(code, Tag) else pre
        target.insert(0, sentinel)

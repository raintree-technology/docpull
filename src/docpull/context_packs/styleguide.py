"""Local design-token and styleguide context packs."""

from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ..policy import PolicyConfig, policy_domain_matches
from ..security.url_validator import UrlValidator
from .common import (
    CONTEXT_PACK_SCHEMA_VERSION,
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    artifact_ref,
    asset_allowed_domains_for_domain,
    domain_from_input,
    ensure_policy_for_domain,
    fetch_context_asset_bytes,
    fetch_pages_blocking,
    homepage_url_for_domain,
    public_url,
    quote_markdown,
    resolve_url,
    soup_for,
    status_from_errors,
    write_basic_pack_files,
    write_json,
)

STYLEGUIDE_WORKFLOW = "styleguide-pack"
DEFAULT_STYLEGUIDE_OUTPUT_DIR = Path("packs/styleguide")
MAX_STYLESHEET_BYTES = 500_000
MAX_STYLESHEETS = 12
COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]+\)|hsla?\([^)]+\)", re.IGNORECASE)
CSS_VAR_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;}{]+)")
FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)
SPACING_RE = re.compile(
    r"(?:margin|padding|gap|inset|top|left|right|bottom)(?:-[A-Za-z]+)?\s*:\s*([^;}{]+)",
    re.IGNORECASE,
)
RADIUS_RE = re.compile(r"border-radius\s*:\s*([^;}{]+)", re.IGNORECASE)
SHADOW_RE = re.compile(r"box-shadow\s*:\s*([^;}{]+)", re.IGNORECASE)
FONT_URL_RE = re.compile(r"url\(([^)]+)\)\s*format\(([^)]+)\)", re.IGNORECASE)


def build_styleguide_pack(
    domain_or_url: str,
    *,
    output_dir: Path = DEFAULT_STYLEGUIDE_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    render: bool = False,
    max_stylesheets: int = MAX_STYLESHEETS,
) -> dict[str, Any]:
    """Build local style tokens from static HTML and CSS evidence."""
    if render and os.environ.get("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS") != "1":
        raise ContextPackError(
            "styleguide-pack rendering requires DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1. "
            "Run browser-free with render=False, or explicitly trust the target."
        )
    domain = domain_from_input(domain_or_url)
    if not domain:
        raise ContextPackError("Could not resolve a domain from input.")
    policy = ensure_policy_for_domain(policy, domain)
    output_dir = output_dir.resolve()
    run = ContextPackRun(
        workflow=STYLEGUIDE_WORKFLOW,
        output_dir=output_dir,
        policy=policy,
        input_value=domain_or_url,
    )
    start_url = public_url(domain_or_url if "://" in domain_or_url else homepage_url_for_domain(domain))
    pages = fetch_pages_blocking([start_url], run=run, max_pages=1)
    if not pages:
        raise ContextPackError(f"Could not fetch styleguide target: {start_url}")
    page = pages[0]
    css_sources = _collect_css_sources(page, domain, max_stylesheets=max_stylesheets)
    css_texts: list[dict[str, str]] = []
    for source in css_sources:
        text = _fetch_css_blocking(source["url"], domain=domain, run=run)
        if text:
            css_texts.append({"url": source["url"], "text": text, "source": source["source"]})
    inline_css = _inline_css(page)
    css_texts.extend({"url": page.url, "text": text, "source": "inline"} for text in inline_css)

    tokens = _extract_tokens(css_texts, page)
    fonts = _extract_fonts(css_texts, page)
    components = _extract_component_samples(page)
    tokens_path = output_dir / "tokens.json"
    css_path = output_dir / "tokens.css"
    fonts_path = output_dir / "fonts.manifest.json"
    components_path = output_dir / "components.samples.json"
    write_json(tokens_path, tokens)
    css_path.write_text(_tokens_css(tokens), encoding="utf-8")
    write_json(fonts_path, fonts)
    write_json(components_path, components)

    result_payload = {
        "workflow": STYLEGUIDE_WORKFLOW,
        "provider": "local",
        "status": status_from_errors(run.errors),
        "input": {"value": public_url(domain_or_url), "domain": domain, "render": render},
        "summary": {
            "domain": domain,
            "stylesheet_count": len(css_texts),
            "css_variable_count": len(tokens["css_variables"]),
            "color_count": len(tokens["colors"]),
            "font_family_count": len(fonts["font_families"]),
            "component_sample_count": len(components["samples"]),
        },
        "tokens": tokens,
        "fonts": fonts,
        "components": components,
        "warnings": run.warnings,
        "errors": run.errors,
        "replay_config": {
            "domain_or_url": domain_or_url,
            "render": render,
            "max_stylesheets": max_stylesheets,
        },
    }
    return write_basic_pack_files(
        run=run,
        pages=pages,
        result_filename="styleguide.result.json",
        result_payload=result_payload,
        markdown_filename="STYLEGUIDE.md",
        markdown_text=_styleguide_markdown(tokens, fonts, components, page),
        pack_filename="styleguide.pack.json",
        extra_artifacts={
            "tokens_json": artifact_ref(output_dir, tokens_path),
            "tokens_css": artifact_ref(output_dir, css_path),
            "fonts_manifest": artifact_ref(output_dir, fonts_path),
            "components_samples": artifact_ref(output_dir, components_path),
        },
    )


def _collect_css_sources(
    page: PageSnapshot,
    domain: str,
    *,
    max_stylesheets: int,
) -> list[dict[str, str]]:
    soup = soup_for(page)
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for tag in soup.find_all("link"):
        rel = " ".join(str(item).lower() for item in tag.get("rel", []))
        href = str(tag.get("href") or "").strip()
        if not href or "stylesheet" not in rel:
            continue
        url = resolve_url(href, page.url)
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if not host or not policy_domain_matches(host, domain):
            continue
        if url not in seen:
            sources.append({"url": url, "source": "linked_stylesheet"})
            seen.add(url)
        if len(sources) >= max_stylesheets:
            break
    return sources


def _inline_css(page: PageSnapshot) -> list[str]:
    soup = soup_for(page)
    blocks = [style.get_text() for style in soup.find_all("style") if style.get_text().strip()]
    for tag in soup.find_all(True):
        style = str(tag.get("style") or "").strip()
        if style:
            blocks.append(style)
    return blocks


async def _fetch_css(url: str, *, domain: str, run: ContextPackRun) -> str | None:
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(url)
    if not validation.is_valid:
        run.warn(
            "stylesheet_rejected",
            "Stylesheet URL rejected.",
            url=url,
            reason=validation.rejection_reason,
        )
        return None
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    allowed_domains = asset_allowed_domains_for_domain(domain)
    if not host or not any(policy_domain_matches(host, allowed) for allowed in allowed_domains):
        run.warn("stylesheet_rejected", "Stylesheet outside allowed domain.", url=url)
        return None
    try:
        response = await fetch_context_asset_bytes(
            url,
            allowed_domains=allowed_domains,
            allowed_content_types={"text/css", "text/plain"},
            max_bytes=MAX_STYLESHEET_BYTES,
        )
        run.http_request_count += 1
        if response.status_code >= 400:
            run.warn("stylesheet_failed", "Stylesheet fetch failed.", url=url, status=response.status_code)
            return None
        return response.content.decode("utf-8", errors="replace")
    except Exception as err:  # noqa: BLE001
        run.warn("stylesheet_failed", "Stylesheet fetch failed.", url=url, error=str(err)[:200])
        return None


def _fetch_css_blocking(url: str, *, domain: str, run: ContextPackRun) -> str | None:
    return asyncio.run(_fetch_css(url, domain=domain, run=run))


def _extract_tokens(css_sources: list[dict[str, str]], page: PageSnapshot) -> dict[str, Any]:
    combined = "\n".join(source["text"] for source in css_sources)
    variables: dict[str, dict[str, Any]] = {}
    for source in css_sources:
        for name, value in CSS_VAR_RE.findall(source["text"]):
            variables.setdefault(
                name,
                {
                    "name": name,
                    "value": value.strip(),
                    "source_url": source["url"],
                },
            )
    colors = _rank_values(COLOR_RE.findall(combined), limit=24)
    spacing = _rank_values(_split_css_values(SPACING_RE.findall(combined)), limit=24)
    radii = _rank_values(_split_css_values(RADIUS_RE.findall(combined)), limit=16)
    shadows = _rank_values([value.strip() for value in SHADOW_RE.findall(combined)], limit=12)
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": _generated_at(),
        "source_url": page.url,
        "css_variables": list(variables.values())[:200],
        "colors": colors,
        "spacing": spacing,
        "radii": radii,
        "shadows": shadows,
    }


def _extract_fonts(css_sources: list[dict[str, str]], page: PageSnapshot) -> dict[str, Any]:
    combined = "\n".join(source["text"] for source in css_sources)
    font_families = _rank_values(
        [_clean_font_stack(value) for value in FONT_FAMILY_RE.findall(combined)],
        limit=24,
    )
    font_urls = []
    for source in css_sources:
        for match in FONT_URL_RE.finditer(source["text"]):
            raw_url = match.group(1).strip().strip("'\"")
            font_urls.append(
                {
                    "url": public_url(urljoin(source["url"], raw_url)),
                    "format": match.group(2).strip().strip("'\""),
                    "source_url": source["url"],
                    "downloaded": False,
                }
            )
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": _generated_at(),
        "source_url": page.url,
        "font_families": font_families,
        "font_urls": font_urls[:100],
        "download_policy": "remote fonts are recorded but not downloaded by default",
    }


def _extract_component_samples(page: PageSnapshot) -> dict[str, Any]:
    soup = soup_for(page)
    samples: list[dict[str, Any]] = []
    selectors = [
        ("button", "button"),
        ("a", "link"),
        ("[class*=card i]", "card"),
        ("[class*=btn i]", "button"),
        ("[class*=button i]", "button"),
    ]
    seen: set[str] = set()
    for selector, kind in selectors:
        for tag in soup.select(selector)[:12]:
            classes = " ".join(str(item) for item in tag.get("class", []))
            text = " ".join(tag.get_text(" ").split())[:120]
            style = str(tag.get("style") or "")
            key = f"{kind}:{classes}:{text}:{style}"
            if key in seen:
                continue
            seen.add(key)
            samples.append(
                {
                    "kind": kind,
                    "tag": tag.name,
                    "text": text,
                    "class": classes,
                    "id": str(tag.get("id") or ""),
                    "style": style,
                }
            )
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": _generated_at(),
        "source_url": page.url,
        "samples": samples[:40],
    }


def _rank_values(values: list[str], *, limit: int) -> list[dict[str, Any]]:
    cleaned = [value.strip() for value in values if value and value.strip() and len(value.strip()) <= 160]
    counter = Counter(cleaned)
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def _split_css_values(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        for item in re.split(r"\s+", value.strip()):
            if item and re.search(r"\d", item):
                output.append(item)
    return output


def _clean_font_stack(value: str) -> str:
    return " ".join(value.replace("\n", " ").split()).strip()


def _tokens_css(tokens: dict[str, Any]) -> str:
    lines = [":root {"]
    variables = tokens.get("css_variables")
    if isinstance(variables, list) and variables:
        for item in variables:
            if isinstance(item, dict) and item.get("name") and item.get("value"):
                lines.append(f"  {item['name']}: {item['value']};")
    else:
        for index, item in enumerate(tokens.get("colors", []), start=1):
            if isinstance(item, dict) and item.get("value"):
                lines.append(f"  --docpull-color-{index}: {item['value']};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _styleguide_markdown(
    tokens: dict[str, Any],
    fonts: dict[str, Any],
    components: dict[str, Any],
    page: PageSnapshot,
) -> str:
    lines = [f"# Styleguide: {page.title or page.url}", ""]
    lines.append("## Colors")
    for item in tokens.get("colors", [])[:12]:
        if isinstance(item, dict):
            lines.append(f"- `{quote_markdown(str(item.get('value')))}` ({item.get('count', 1)})")
    if len(lines) == 3:
        lines.append("- No color tokens found.")
    lines.append("")
    lines.append("## Fonts")
    font_families = fonts.get("font_families") if isinstance(fonts.get("font_families"), list) else []
    if font_families:
        for item in font_families[:12]:
            if isinstance(item, dict):
                lines.append(f"- `{quote_markdown(str(item.get('value')))}` ({item.get('count', 1)})")
    else:
        lines.append("- No font stacks found.")
    lines.append("")
    lines.append("## Component Samples")
    samples = components.get("samples") if isinstance(components.get("samples"), list) else []
    if samples:
        for item in samples[:16]:
            if isinstance(item, dict):
                label = item.get("text") or item.get("class") or item.get("tag")
                lines.append(f"- {quote_markdown(str(item.get('kind')))}: {quote_markdown(str(label))}")
    else:
        lines.append("- No component samples found.")
    return "\n".join(lines)


def _generated_at() -> str:
    from ..time_utils import utc_now_iso

    return utc_now_iso()

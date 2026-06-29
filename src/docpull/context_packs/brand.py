"""Local evidence-backed brand context packs."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..policy import PolicyConfig
from .common import (
    CONTEXT_PACK_SCHEMA_VERSION,
    AssetRef,
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    artifact_ref,
    domain_from_email,
    domain_from_input,
    ensure_policy_for_domain,
    evidence_for_page,
    fetch_asset_blocking,
    fetch_pages_blocking,
    homepage_url_for_domain,
    is_free_or_disposable_email_domain,
    likely_internal_pages,
    public_url,
    quote_markdown,
    resolve_url,
    same_policy_domain,
    soup_for,
    status_from_errors,
    text_excerpt,
    write_basic_pack_files,
    write_json,
)

BRAND_WORKFLOW = "brand-pack"
DEFAULT_BRAND_OUTPUT_DIR = Path("packs/brand")
MAX_BRAND_PAGES = 6
MAX_LOGO_CANDIDATES = 8
IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/x-icon"}
SOCIAL_DOMAINS = {
    "facebook.com": "facebook",
    "github.com": "github",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "medium.com": "medium",
    "threads.net": "threads",
    "tiktok.com": "tiktok",
    "twitter.com": "twitter",
    "x.com": "x",
    "youtube.com": "youtube",
}
COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]+\)|hsla?\([^)]+\)",
    re.IGNORECASE,
)


def build_brand_pack(
    domain_or_url: str,
    *,
    email: str | None = None,
    name: str | None = None,
    ticker: str | None = None,
    output_dir: Path = DEFAULT_BRAND_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    allow_free_email: bool = False,
    download_assets: bool = True,
    max_pages: int = MAX_BRAND_PAGES,
) -> dict[str, Any]:
    """Build a local brand profile from public website evidence."""
    domain = _resolve_brand_domain(domain_or_url, email=email, allow_free_email=allow_free_email)
    policy = ensure_policy_for_domain(policy, domain)
    output_dir = output_dir.resolve()
    run = ContextPackRun(
        workflow=BRAND_WORKFLOW,
        output_dir=output_dir,
        policy=policy,
        input_value=email or domain_or_url,
    )
    home_url = homepage_url_for_domain(domain)
    home_pages = fetch_pages_blocking([home_url], run=run, max_pages=1)
    if not home_pages:
        raise ContextPackError(f"Could not fetch homepage for {domain}.")
    candidate_urls = likely_internal_pages(home_pages[0], domain, max_pages=max_pages)
    pages = fetch_pages_blocking(candidate_urls, run=run, max_pages=max_pages)
    if not pages:
        pages = home_pages

    profile = _extract_brand_profile(pages, domain=domain, name_hint=name, ticker=ticker)
    assets = _extract_brand_assets(
        pages,
        domain=domain,
        output_dir=output_dir,
        download_assets=download_assets,
        run=run,
    )
    profile["logos"] = assets["logos"]
    profile["icons"] = assets["icons"]
    profile["colors"] = _rank_brand_colors(profile.get("colors", []) + assets["colors"])
    profile["social_links"] = _extract_social_links(pages)
    profile["contact_links"] = _extract_contact_links(pages, domain)
    profile["firmographics"] = _local_firmographics(pages)

    result_payload = {
        "workflow": BRAND_WORKFLOW,
        "provider": "local",
        "status": status_from_errors(run.errors),
        "input": {
            "value": public_url(domain_or_url),
            "domain": domain,
            "email": email,
            "name": name,
            "ticker": ticker,
        },
        "summary": {
            "domain": domain,
            "page_count": len(pages),
            "logo_candidate_count": len(profile["logos"]),
            "color_count": len(profile["colors"]),
            "social_link_count": len(profile["social_links"]),
        },
        "brand": profile,
        "assets": assets,
        "warnings": run.warnings,
        "errors": run.errors,
        "replay_config": {
            "domain_or_url": domain_or_url,
            "email": email,
            "name": name,
            "ticker": ticker,
            "allow_free_email": allow_free_email,
            "download_assets": download_assets,
            "max_pages": max_pages,
        },
    }
    assets_path = output_dir / "brand.assets.json"
    write_json(assets_path, assets)
    return write_basic_pack_files(
        run=run,
        pages=pages,
        result_filename="brand.result.json",
        result_payload=result_payload,
        markdown_filename="BRAND.md",
        markdown_text=_brand_markdown(profile, pages),
        pack_filename="brand.pack.json",
        extra_artifacts={"brand_assets": artifact_ref(output_dir, assets_path)},
    )


def _resolve_brand_domain(
    domain_or_url: str,
    *,
    email: str | None,
    allow_free_email: bool,
) -> str:
    if email:
        domain = domain_from_email(email)
        if not domain:
            raise ContextPackError("email must be a valid email address.")
        if is_free_or_disposable_email_domain(domain) and not allow_free_email:
            raise ContextPackError(
                "Free or disposable email domains are not accepted for work-email brand enrichment. "
                "Pass allow_free_email=True or CLI --allow-free-email to override."
            )
        return domain
    domain = domain_from_input(domain_or_url)
    if not domain:
        raise ContextPackError("Could not resolve a domain from input.")
    return domain


def _extract_brand_profile(
    pages: list[PageSnapshot],
    *,
    domain: str,
    name_hint: str | None,
    ticker: str | None,
) -> dict[str, Any]:
    homepage = pages[0]
    org = _first_jsonld_org(pages)
    og = _metadata_from_page(homepage)
    title = _first_text(
        org.get("name"),
        og.get("site_name"),
        og.get("title"),
        homepage.title,
        name_hint,
    )
    description = _first_text(
        org.get("description"),
        og.get("description"),
        _meta_content(soup_for(homepage), "description"),
    )
    slogan = _first_text(org.get("slogan"), _slogan_heading(homepage, title))
    colors = _metadata_colors(homepage)
    evidence = []
    if title:
        evidence.append(evidence_for_page(homepage, pages, field="title", excerpt=title).to_dict())
    if description:
        evidence.append(
            evidence_for_page(homepage, pages, field="description", excerpt=description).to_dict()
        )
    return {
        "domain": domain,
        "name": title,
        "description": description,
        "slogan": slogan,
        "canonical_url": public_url(_first_text(og.get("url"), homepage.url) or homepage.url),
        "ticker": ticker,
        "source_status": "locally_cited",
        "colors": colors,
        "evidence": evidence,
        "jsonld_organization": org,
    }


def _metadata_from_page(page: PageSnapshot) -> dict[str, str]:
    metadata = page.metadata or {}
    result: dict[str, str] = {}
    mapping = {
        "title": ("title", "og:title", "twitter:title"),
        "description": ("description", "og:description", "twitter:description"),
        "site_name": ("site_name", "og:site_name"),
        "image": ("image", "og:image", "twitter:image"),
        "url": ("canonical_url", "og:url"),
    }
    for output_key, keys in mapping.items():
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                result[output_key] = value.strip()
                break
    soup = soup_for(page)
    for output_key, meta_names in {
        "title": ("og:title", "twitter:title"),
        "description": ("og:description", "twitter:description", "description"),
        "site_name": ("og:site_name",),
        "image": ("og:image", "twitter:image"),
        "url": ("og:url",),
    }.items():
        if output_key not in result:
            value = _meta_content(soup, *meta_names)
            if value:
                result[output_key] = value
    return result


def _first_jsonld_org(pages: list[PageSnapshot]) -> dict[str, Any]:
    for page in pages:
        soup = soup_for(page)
        for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
            text = script.string or script.get_text()
            for item in _jsonld_items(text):
                org = _org_from_jsonld(item)
                if org:
                    return org
    return {}


def _jsonld_items(text: str) -> list[Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return []
    items: list[Any] = []
    queue = [raw]
    while queue:
        item = queue.pop(0)
        if isinstance(item, list):
            queue.extend(item)
        elif isinstance(item, dict):
            items.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(graph)
    return items


def _org_from_jsonld(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    raw_type = item.get("@type")
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    normalized = {str(value).lower() for value in types if value is not None}
    if not normalized & {"organization", "corporation", "localbusiness", "brand", "store"}:
        return {}
    fields = (
        "name",
        "legalName",
        "alternateName",
        "description",
        "slogan",
        "url",
        "logo",
        "image",
        "sameAs",
        "email",
        "telephone",
        "address",
        "foundingDate",
    )
    return {field: item[field] for field in fields if field in item}


def _extract_brand_assets(
    pages: list[PageSnapshot],
    *,
    domain: str,
    output_dir: Path,
    download_assets: bool,
    run: ContextPackRun,
) -> dict[str, Any]:
    logos: list[AssetRef] = []
    icons: list[AssetRef] = []
    colors: list[str] = []
    seen: set[str] = set()
    for page in pages:
        soup = soup_for(page)
        colors.extend(_colors_from_soup(soup))
        for url, kind in _logo_candidate_urls(page):
            if url in seen:
                continue
            seen.add(url)
            asset_ref = AssetRef(url, kind, page.url)
            if download_assets and len(logos) < MAX_LOGO_CANDIDATES:
                asset_ref = fetch_asset_blocking(
                    url,
                    output_dir=output_dir / "assets" / "logos",
                    source_url=page.url,
                    kind=kind,
                    allowed_domains=[domain],
                    allowed_content_types=IMAGE_CONTENT_TYPES,
                    max_bytes=500_000,
                    run=run,
                )
            if kind == "icon":
                icons.append(asset_ref)
            else:
                logos.append(asset_ref)
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": _generated_at(),
        "logos": [item.to_dict() for item in logos[:MAX_LOGO_CANDIDATES]],
        "icons": [item.to_dict() for item in icons[:MAX_LOGO_CANDIDATES]],
        "colors": _rank_brand_colors(colors),
    }


def _logo_candidate_urls(page: PageSnapshot) -> list[tuple[str, str]]:
    soup = soup_for(page)
    urls: list[tuple[str, str]] = []
    metadata = _metadata_from_page(page)
    if metadata.get("image"):
        urls.append((resolve_url(metadata["image"], page.url), "logo"))
    org = _first_jsonld_org([page])
    for field in ("logo", "image"):
        value = org.get(field)
        if isinstance(value, str):
            urls.append((resolve_url(value, page.url), "logo"))
        elif isinstance(value, dict) and isinstance(value.get("url"), str):
            urls.append((resolve_url(value["url"], page.url), "logo"))
    for tag in soup.find_all("link"):
        rel = " ".join(str(item).lower() for item in tag.get("rel", []))
        href = str(tag.get("href") or "").strip()
        if not href:
            continue
        if "icon" in rel:
            urls.append((resolve_url(href, page.url), "icon"))
        elif "logo" in rel:
            urls.append((resolve_url(href, page.url), "logo"))
    for tag in soup.find_all("img"):
        src = str(tag.get("src") or "").strip()
        if not src:
            continue
        label = " ".join(str(tag.get(name) or "").lower() for name in ("alt", "class", "id", "aria-label"))
        if "logo" in label or "brand" in label:
            urls.append((resolve_url(src, page.url), "logo"))
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, kind in urls:
        if url not in seen:
            deduped.append((url, kind))
            seen.add(url)
    return deduped


def _extract_social_links(pages: list[PageSnapshot]) -> list[dict[str, str]]:
    links: dict[str, dict[str, str]] = {}
    for page in pages:
        for tag in soup_for(page).find_all("a"):
            href = str(tag.get("href") or "").strip()
            if not href:
                continue
            url = public_url(urljoin(page.url, href))
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            for domain, network in SOCIAL_DOMAINS.items():
                if host == domain or host.endswith(f".{domain}"):
                    links[network] = {"network": network, "url": url}
    return sorted(links.values(), key=lambda item: item["network"])


def _extract_contact_links(pages: list[PageSnapshot], domain: str) -> list[dict[str, str]]:
    contacts: list[dict[str, str]] = []
    seen: set[str] = set()
    for page in pages:
        for tag in soup_for(page).find_all("a"):
            href = str(tag.get("href") or "").strip()
            text = " ".join(tag.get_text(" ").split())
            if not href:
                continue
            if href.startswith("mailto:"):
                value = href.split("?", 1)[0]
                if value not in seen:
                    contacts.append({"type": "email", "value": value, "source_url": page.url})
                    seen.add(value)
                continue
            url = public_url(urljoin(page.url, href))
            path = (urlparse(url).path or "").lower()
            is_contact_url = same_policy_domain(url, domain) and ("contact" in path or "support" in path)
            if is_contact_url and url not in seen:
                contacts.append({"type": "page", "value": url, "label": text, "source_url": page.url})
                seen.add(url)
    return contacts[:20]


def _local_firmographics(pages: list[PageSnapshot]) -> dict[str, Any]:
    firmographics: dict[str, Any] = {}
    for page in pages:
        soup = soup_for(page)
        text = soup.get_text(" ")
        founded = re.search(r"\bfounded\s+(?:in\s+)?(\d{4})\b", text, flags=re.IGNORECASE)
        if founded and "founded_year" not in firmographics:
            firmographics["founded_year"] = {
                "value": int(founded.group(1)),
                "evidence": evidence_for_page(
                    page,
                    pages,
                    field="founded_year",
                    excerpt=text_excerpt(text, founded.group(0)),
                ).to_dict(),
            }
    return firmographics


def _colors_from_soup(soup: BeautifulSoup) -> list[str]:
    colors: list[str] = []
    for tag in soup.find_all(True):
        style = str(tag.get("style") or "")
        colors.extend(match.group(0) for match in COLOR_RE.finditer(style))
    for style_tag in soup.find_all("style"):
        colors.extend(match.group(0) for match in COLOR_RE.finditer(style_tag.get_text()))
    return colors


def _metadata_colors(page: PageSnapshot) -> list[dict[str, Any]]:
    soup = soup_for(page)
    colors: list[str] = []
    for name in ("theme-color", "msapplication-TileColor"):
        value = _meta_content(soup, name)
        if value:
            colors.append(value)
    colors.extend(_colors_from_soup(soup))
    return _rank_brand_colors(colors)


def _rank_brand_colors(colors: list[Any]) -> list[dict[str, Any]]:
    normalized: list[str] = []
    for color in colors:
        if isinstance(color, dict):
            value = color.get("value")
            if isinstance(value, str):
                normalized.extend([value] * int(color.get("count", 1)))
        elif isinstance(color, str):
            normalized.append(color)
    cleaned = [color.strip() for color in normalized if color and len(color.strip()) <= 80]
    counts = Counter(cleaned)
    return [{"value": color, "count": count} for color, count in counts.most_common(12)]


def _slogan_heading(page: PageSnapshot, title: str | None) -> str | None:
    soup = soup_for(page)
    for tag_name in ("h1", "h2"):
        for tag in soup.find_all(tag_name):
            text = " ".join(tag.get_text(" ").split())
            if not text or text == title or len(text) > 140:
                continue
            return text
    return None


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    targets = {name.lower() for name in names}
    for tag in soup.find_all("meta"):
        name = str(tag.get("name") or tag.get("property") or "").lower()
        if name in targets:
            content = str(tag.get("content") or "").strip()
            if content:
                return content
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _brand_markdown(profile: dict[str, Any], pages: list[PageSnapshot]) -> str:
    lines = [
        f"# {profile.get('name') or profile.get('domain')}",
        "",
        f"- Domain: `{profile.get('domain')}`",
    ]
    if profile.get("description"):
        lines.append(f"- Description: {profile['description']}")
    if profile.get("slogan"):
        lines.append(f"- Slogan: {profile['slogan']}")
    if profile.get("canonical_url"):
        lines.append(f"- Canonical URL: {profile['canonical_url']}")
    lines.append("")
    lines.append("## Colors")
    colors = profile.get("colors") if isinstance(profile.get("colors"), list) else []
    if colors:
        for color in colors[:8]:
            if isinstance(color, dict):
                value = quote_markdown(str(color.get("value")))
                lines.append(f"- `{value}` ({color.get('count', 1)})")
    else:
        lines.append("- No local color evidence found.")
    lines.append("")
    lines.append("## Social Links")
    socials = profile.get("social_links") if isinstance(profile.get("social_links"), list) else []
    if socials:
        for item in socials:
            lines.append(f"- {quote_markdown(item.get('network'))}: {item.get('url')}")
    else:
        lines.append("- No social links found.")
    lines.append("")
    lines.append("## Evidence")
    for index, page in enumerate(pages, start=1):
        lines.append(f"- [S{index}] [{quote_markdown(page.title or page.url)}]({page.url})")
    return "\n".join(lines)


def _generated_at() -> str:
    from ..time_utils import utc_now_iso

    return utc_now_iso()

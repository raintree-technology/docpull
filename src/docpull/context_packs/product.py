"""Local product and pricing context packs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import Tag

from ..policy import PolicyConfig
from .common import (
    CONTEXT_PACK_SCHEMA_VERSION,
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    append_ndjson,
    artifact_ref,
    domain_from_input,
    ensure_policy_for_domain,
    evidence_for_page,
    fetch_pages_blocking,
    homepage_url_for_domain,
    public_url,
    quote_markdown,
    same_policy_domain,
    soup_for,
    status_from_errors,
    text_excerpt,
    write_basic_pack_files,
    write_json,
)

PRODUCT_WORKFLOW = "product-pack"
DEFAULT_PRODUCT_OUTPUT_DIR = Path("packs/products")
PRICE_RE = re.compile(
    r"(?P<currency>\$|USD|EUR|GBP|€|£)\s?(?P<amount>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?/(?P<period>mo|month|monthly|yr|year|yearly|user|seat))?",
    re.IGNORECASE,
)
PRODUCT_LINK_KEYWORDS = ("pricing", "product", "products", "plans", "shop", "store")


def build_product_pack(
    url_or_domain: str,
    *,
    mode: str = "page",
    output_dir: Path = DEFAULT_PRODUCT_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    max_pages: int = 8,
) -> dict[str, Any]:
    """Build cited product/pricing records from a page or bounded site discovery."""
    if mode not in {"page", "site"}:
        raise ContextPackError("product-pack mode must be 'page' or 'site'.")
    domain = domain_from_input(url_or_domain)
    if not domain:
        raise ContextPackError("Could not resolve a domain from input.")
    policy = ensure_policy_for_domain(policy, domain)
    run = ContextPackRun(
        workflow=PRODUCT_WORKFLOW,
        output_dir=output_dir.resolve(),
        policy=policy,
        input_value=url_or_domain,
    )
    start_url = public_url(url_or_domain if "://" in url_or_domain else homepage_url_for_domain(domain))
    urls = [start_url]
    if mode == "site":
        home = fetch_pages_blocking([start_url], run=run, max_pages=1)
        if home:
            urls = _site_product_urls(home[0], domain, max_pages=max_pages)
    pages = fetch_pages_blocking(urls, run=run, max_pages=max_pages if mode == "site" else 1)
    if not pages:
        raise ContextPackError(f"Could not fetch product target: {start_url}")

    products: list[dict[str, Any]] = []
    pricing_rows: list[dict[str, Any]] = []
    for page in pages:
        extracted = _extract_products_from_page(page, pages)
        products.extend(extracted["products"])
        pricing_rows.extend(extracted["pricing_rows"])
    if not products:
        products.append(_non_product_record(pages[0], pages))

    products_path = run.output_dir / "products.ndjson"
    pricing_path = run.output_dir / "pricing.matrix.json"
    append_ndjson(products_path, products)
    write_json(
        pricing_path,
        {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "generated_at": _generated_at(),
            "row_count": len(pricing_rows),
            "rows": pricing_rows,
        },
    )
    result_payload = {
        "workflow": PRODUCT_WORKFLOW,
        "provider": "local",
        "status": status_from_errors(run.errors),
        "input": {"value": public_url(url_or_domain), "domain": domain, "mode": mode},
        "summary": {
            "domain": domain,
            "mode": mode,
            "page_count": len(pages),
            "product_count": len(products),
            "pricing_row_count": len(pricing_rows),
            "is_product_page": any(bool(item.get("is_product_page")) for item in products),
        },
        "products": products,
        "pricing_matrix": pricing_rows,
        "warnings": run.warnings,
        "errors": run.errors,
        "replay_config": {"url_or_domain": url_or_domain, "mode": mode, "max_pages": max_pages},
    }
    return write_basic_pack_files(
        run=run,
        pages=pages,
        result_filename="products.result.json",
        result_payload=result_payload,
        markdown_filename="PRODUCTS.md",
        markdown_text=_products_markdown(products, pricing_rows, pages),
        pack_filename="products.pack.json",
        extra_artifacts={
            "products_ndjson": artifact_ref(run.output_dir, products_path),
            "pricing_matrix": artifact_ref(run.output_dir, pricing_path),
        },
    )


def _site_product_urls(page: PageSnapshot, domain: str, *, max_pages: int) -> list[str]:
    soup = soup_for(page)
    urls = [page.url]
    seen = {page.url}
    for tag in soup.find_all("a"):
        href = str(tag.get("href") or "").strip()
        text = " ".join(tag.get_text(" ").split()).lower()
        if not href:
            continue
        url = public_url(str(page.url if href.startswith("#") else urljoin(page.url, href)))
        path = (urlparse(url).path or "").lower()
        if not same_policy_domain(url, domain):
            continue
        if any(keyword in path or keyword in text for keyword in PRODUCT_LINK_KEYWORDS) and url not in seen:
            urls.append(url)
            seen.add(url)
        if len(urls) >= max_pages:
            break
    return urls


def _extract_products_from_page(page: PageSnapshot, pages: list[PageSnapshot]) -> dict[str, Any]:
    jsonld_products = _jsonld_products(page, pages)
    pricing_rows = _pricing_rows(page, pages)
    if jsonld_products:
        for product in jsonld_products:
            if pricing_rows and not product.get("offers"):
                product["offers"] = pricing_rows
        return {"products": jsonld_products, "pricing_rows": pricing_rows}
    if pricing_rows:
        product = {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "source_url": page.url,
            "name": _page_heading(page) or page.title,
            "description": _meta_description(page),
            "is_product_page": True,
            "evidence_status": "heuristic_pricing_evidence",
            "offers": pricing_rows,
            "features": _feature_bullets(page),
            "images": _image_candidates(page),
            "citations": [
                evidence_for_page(
                    page,
                    pages,
                    field="pricing",
                    excerpt=text_excerpt(page.markdown),
                ).to_dict()
            ],
        }
        return {"products": [product], "pricing_rows": pricing_rows}
    return {"products": [], "pricing_rows": []}


def _jsonld_products(page: PageSnapshot, pages: list[PageSnapshot]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for item in _jsonld_items(page):
        if not _jsonld_type_in(item, {"product", "softwareapplication", "service"}):
            continue
        offers = _offers_from_jsonld(item.get("offers"), page, pages)
        product = {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "source_url": page.url,
            "name": _string_or_none(item.get("name")),
            "description": _string_or_none(item.get("description")),
            "sku": _string_or_none(item.get("sku")),
            "category": _string_or_none(item.get("category")),
            "brand": _jsonld_name(item.get("brand")),
            "is_product_page": True,
            "evidence_status": "jsonld_product",
            "offers": offers,
            "features": _feature_bullets(page),
            "images": _image_candidates(page, item.get("image")),
            "citations": [
                evidence_for_page(
                    page,
                    pages,
                    field="jsonld_product",
                    excerpt=_string_or_none(item.get("name")) or page.title or page.url,
                ).to_dict()
            ],
        }
        products.append(product)
    return products


def _offers_from_jsonld(raw: Any, page: PageSnapshot, pages: list[PageSnapshot]) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else [raw]
    offers: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        price = _parse_price_value(item.get("price"))
        offers.append(
            {
                "name": _string_or_none(item.get("name")),
                "price": price,
                "currency": _string_or_none(item.get("priceCurrency")),
                "billing_frequency": _billing_from_text(json.dumps(item, ensure_ascii=False)),
                "availability": _string_or_none(item.get("availability")),
                "url": public_url(str(item.get("url") or page.url)),
                "evidence": evidence_for_page(
                    page,
                    pages,
                    field="offer",
                    excerpt=json.dumps(item)[:240],
                ).to_dict(),
            }
        )
    return offers


def _pricing_rows(page: PageSnapshot, pages: list[PageSnapshot]) -> list[dict[str, Any]]:
    soup = soup_for(page)
    rows: list[dict[str, Any]] = []
    text_blocks: list[str] = []
    for selector in ("table tr", '[class*="pricing" i]', '[class*="plan" i]', '[class*="price" i]'):
        tags: list[Any]
        try:
            tags = list(soup.select(selector))
        except Exception:  # noqa: BLE001
            tags = []
        for tag in tags[:80]:
            text = " ".join(tag.get_text(" ").split())
            if PRICE_RE.search(text):
                text_blocks.append(text)
    if not text_blocks:
        text_blocks = [line.strip() for line in page.markdown.splitlines() if PRICE_RE.search(line)]
    seen: set[str] = set()
    for block in text_blocks:
        if block in seen:
            continue
        seen.add(block)
        match = PRICE_RE.search(block)
        if not match:
            continue
        rows.append(
            {
                "plan_name": _plan_name_from_text(block),
                "price": _parse_price_value(match.group("amount")),
                "currency": _currency_code(match.group("currency")),
                "billing_frequency": _billing_from_text(block) or match.group("period"),
                "raw_text": block[:500],
                "source_url": page.url,
                "evidence": evidence_for_page(page, pages, field="pricing", excerpt=block[:240]).to_dict(),
            }
        )
    return rows[:60]


def _non_product_record(page: PageSnapshot, pages: list[PageSnapshot]) -> dict[str, Any]:
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "source_url": page.url,
        "name": page.title,
        "description": _meta_description(page),
        "is_product_page": False,
        "evidence_status": "no_product_evidence",
        "offers": [],
        "features": [],
        "images": _image_candidates(page),
        "citations": [
            evidence_for_page(
                page,
                pages,
                field="page",
                excerpt=text_excerpt(page.markdown),
            ).to_dict()
        ],
    }


def _jsonld_items(page: PageSnapshot) -> list[Any]:
    soup = soup_for(page)
    items: list[Any] = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            raw = json.loads(script.string or script.get_text())
        except json.JSONDecodeError:
            continue
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


def _jsonld_type_in(item: Any, expected: set[str]) -> bool:
    if not isinstance(item, dict):
        return False
    raw = item.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return any(str(value).lower() in expected for value in values if value is not None)


def _jsonld_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _string_or_none(value.get("name"))
    return None


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int | float):
        return str(value)
    return None


def _parse_price_value(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _currency_code(value: str | None) -> str | None:
    if not value:
        return None
    mapping = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}
    return mapping.get(value.lower(), value.upper())


def _billing_from_text(text: str) -> str | None:
    lowered = text.lower()
    if any(value in lowered for value in ("/mo", "monthly", "per month")):
        return "monthly"
    if any(value in lowered for value in ("/yr", "yearly", "annually", "per year")):
        return "yearly"
    if "one time" in lowered or "one-time" in lowered:
        return "one_time"
    if "usage" in lowered:
        return "usage_based"
    return None


def _plan_name_from_text(text: str) -> str | None:
    before_price = PRICE_RE.split(text, maxsplit=1)[0].strip(" :-|")
    if before_price:
        words = before_price.split()
        return " ".join(words[-5:])
    return None


def _feature_bullets(page: PageSnapshot) -> list[dict[str, str]]:
    soup = soup_for(page)
    features: list[dict[str, str]] = []
    seen: set[str] = set()
    for tag in soup.find_all(["li", "p"]):
        text = " ".join(tag.get_text(" ").split())
        if len(text) < 8 or len(text) > 220 or text in seen:
            continue
        if any(keyword in text.lower() for keyword in ("feature", "include", "support", "unlimited", "api")):
            features.append({"text": text, "source_url": page.url})
            seen.add(text)
        if len(features) >= 30:
            break
    return features


def _image_candidates(page: PageSnapshot, raw_image: Any = None) -> list[dict[str, str]]:
    soup = soup_for(page)
    urls: list[str] = []
    if isinstance(raw_image, str):
        urls.append(raw_image)
    elif isinstance(raw_image, list):
        urls.extend(str(item) for item in raw_image if isinstance(item, str))
    for meta_name in ("og:image", "twitter:image"):
        for tag in soup.find_all("meta"):
            name = str(tag.get("property") or tag.get("name") or "")
            if name == meta_name and tag.get("content"):
                urls.append(str(tag["content"]))
    for tag in soup.find_all("img")[:20]:
        src = str(tag.get("src") or "").strip()
        if src:
            urls.append(src)
    output = []
    seen: set[str] = set()
    for url in urls:
        safe = public_url(urljoin(page.url, url))
        if safe not in seen:
            output.append({"url": safe, "source_url": page.url})
            seen.add(safe)
    return output[:20]


def _page_heading(page: PageSnapshot) -> str | None:
    soup = soup_for(page)
    tag = soup.find(["h1", "h2"])
    if tag:
        text = " ".join(tag.get_text(" ").split())
        return text or None
    return None


def _meta_description(page: PageSnapshot) -> str | None:
    metadata = page.metadata or {}
    for key in ("description", "og:description"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    soup = soup_for(page)
    tag = soup.find("meta", attrs={"name": "description"})
    if not isinstance(tag, Tag):
        return None
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


def _products_markdown(
    products: list[dict[str, Any]],
    pricing_rows: list[dict[str, Any]],
    pages: list[PageSnapshot],
) -> str:
    lines = ["# Products", ""]
    for product in products:
        name = quote_markdown(str(product.get("name") or "Unnamed product"))
        lines.append(f"## {name}")
        lines.append(f"- Source: {product.get('source_url')}")
        lines.append(f"- Product page: {str(bool(product.get('is_product_page'))).lower()}")
        if product.get("description"):
            lines.append(f"- Description: {quote_markdown(str(product['description']))}")
        raw_offers = product.get("offers")
        offers: list[Any] = raw_offers if isinstance(raw_offers, list) else []
        for offer in offers[:10]:
            if isinstance(offer, dict):
                price = offer.get("price")
                currency = offer.get("currency")
                cadence = offer.get("billing_frequency")
                display_price = price if price is not None else "unknown"
                lines.append(f"- Offer: {currency or ''} {display_price} {cadence or ''}")
        lines.append("")
    if pricing_rows:
        lines.append("## Pricing Matrix")
        for row in pricing_rows[:30]:
            lines.append(
                f"- {quote_markdown(str(row.get('plan_name') or 'Plan'))}: "
                f"{row.get('currency') or ''} "
                f"{row.get('price') if row.get('price') is not None else 'unknown'}"
            )
    lines.append("")
    lines.append("## Evidence")
    for index, page in enumerate(pages, start=1):
        lines.append(f"- [S{index}] [{quote_markdown(page.title or page.url)}]({page.url})")
    return "\n".join(lines)


def _generated_at() -> str:
    from ..time_utils import utc_now_iso

    return utc_now_iso()

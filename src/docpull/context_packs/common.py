"""Shared helpers for local context-pack workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from ..accounting import RunAccounting, default_route_steps, write_run_accounting
from ..core.fetcher import Fetcher
from ..http.client import AsyncHttpClient
from ..http.rate_limiter import PerHostRateLimiter
from ..models.config import DocpullConfig, ProfileName
from ..models.document import DocumentRecord
from ..models.run import RunIdentity
from ..pack_tools import build_citation_map
from ..policy import PolicyConfig, policy_domain_matches
from ..security.download_policy import SafeDownloadPolicy, UnsafeDownloadError, content_type_base
from ..security.url_validator import UrlValidator
from ..time_utils import utc_now_iso

CONTEXT_PACK_SCHEMA_VERSION = 1
DEFAULT_CONTEXT_MAX_PAGES = 6
DEFAULT_ASSET_MAX_BYTES = 1_000_000
DEFAULT_CSS_MAX_BYTES = 500_000
SECRET_QUERY_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|auth|bearer|cookie|password|secret|session|token)(?:$|[_-])",
    re.IGNORECASE,
)
FREE_EMAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "proton.me",
        "protonmail.com",
        "yahoo.com",
    }
)
DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {
        "10minutemail.com",
        "guerrillamail.com",
        "mailinator.com",
        "temp-mail.org",
        "tempmail.com",
        "yopmail.com",
    }
)
FORBIDDEN_ASSET_SIGNATURES: tuple[tuple[str, bytes, int], ...] = (
    ("windows_executable", b"MZ", 0),
    ("elf_executable", b"\x7fELF", 0),
    ("mach_o_executable", b"\xfe\xed\xfa\xce", 0),
    ("mach_o_executable", b"\xce\xfa\xed\xfe", 0),
    ("mach_o_executable", b"\xfe\xed\xfa\xcf", 0),
    ("mach_o_executable", b"\xcf\xfa\xed\xfe", 0),
    ("zip_archive", b"PK\x03\x04", 0),
    ("zip_archive", b"PK\x05\x06", 0),
    ("rar_archive", b"Rar!\x1a\x07", 0),
    ("seven_zip_archive", b"7z\xbc\xaf\x27\x1c", 0),
    ("gzip_archive", b"\x1f\x8b", 0),
    ("pdf_document", b"%PDF-", 0),
    ("wasm_binary", b"\x00asm", 0),
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
GIF_SIGNATURES = (b"GIF87a", b"GIF89a")
ICO_SIGNATURES = (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00")
SVG_TAG_RE = re.compile(rb"^(?:\xef\xbb\xbf)?\s*(?:<\?xml[^>]*>\s*)?<svg(?:[\s>/]|$)", re.IGNORECASE)


class ContextPackError(RuntimeError):
    """User-facing context-pack workflow error."""


@dataclass(frozen=True)
class PageSnapshot:
    """Fetched page data used by context-pack extractors."""

    url: str
    title: str | None
    html: str
    markdown: str
    metadata: dict[str, Any]
    extraction: dict[str, Any]
    source_type: str | None = None


@dataclass(frozen=True)
class EvidenceRef:
    """Stable evidence reference for local pack fields."""

    citation_id: str
    url: str
    title: str | None = None
    field: str | None = None
    excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "citation_id": self.citation_id,
            "url": public_url(self.url),
        }
        if self.title:
            payload["title"] = self.title
        if self.field:
            payload["field"] = self.field
        if self.excerpt:
            payload["excerpt"] = self.excerpt[:500]
        return payload


@dataclass(frozen=True)
class AssetRef:
    """Local or remote asset reference with provenance and safety metadata."""

    url: str
    kind: str
    source_url: str
    path: str | None = None
    content_type: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    status: str = "candidate"
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": public_url(self.url),
            "kind": self.kind,
            "source_url": public_url(self.source_url),
            "status": self.status,
        }
        for key, value in (
            ("path", self.path),
            ("content_type", self.content_type),
            ("bytes", self.bytes),
            ("sha256", self.sha256),
            ("warning", self.warning),
        ):
            if value is not None:
                payload[key] = value
        return payload


@dataclass(frozen=True)
class RemoteAssetResponse:
    """Bounded asset bytes fetched through docpull's validated HTTP client."""

    url: str
    status_code: int
    content_type: str
    content: bytes


@dataclass
class ContextPackRun:
    """Mutable state collected while building one context pack."""

    workflow: str
    output_dir: Path
    policy: PolicyConfig
    input_value: str
    started_at: str = field(default_factory=utc_now_iso)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    http_request_count: int = 0
    cache_hit_count: int = 0

    def warn(self, code: str, message: str, **metadata: Any) -> None:
        payload: dict[str, Any] = {"code": code, "message": message}
        if metadata:
            payload["metadata"] = jsonable(metadata)
        self.warnings.append(payload)


class ContextAssetDownloadPolicy(SafeDownloadPolicy):
    """Allow bounded CSS/image assets while keeping redirect and MIME checks strict."""

    def __init__(self, *, allowed_domains: list[str], allowed_content_types: set[str]):
        self._allowed_domains = allowed_domains
        self._allowed_content_types = set()
        for content_type in allowed_content_types:
            base_type = content_type_base(content_type)
            if base_type:
                self._allowed_content_types.add(base_type)
        self._current_content_type = ""

    @property
    def allowed_content_types(self) -> set[str]:
        return set(self._allowed_content_types)

    def validate_request_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            raise UnsafeDownloadError("asset_scheme_not_allowed")
        if not allowed_by_domains(url, self._allowed_domains):
            raise UnsafeDownloadError("asset_domain_not_allowed")

    def validate_response_headers(
        self,
        url: str,
        *,
        status_code: int,
        headers: dict[str, str],
        content_type: str | None,
    ) -> None:
        if status_code == 304 or status_code >= 400:
            return

        disposition = _header_value(headers, "Content-Disposition")
        if disposition and "attachment" in disposition.lower():
            raise UnsafeDownloadError(f"asset_attachment_response: {url}")

        base_type = content_type_base(content_type or _header_value(headers, "Content-Type"))
        self._current_content_type = base_type
        if not base_type or base_type not in self._allowed_content_types:
            raise UnsafeDownloadError("asset_content_type_not_allowed")

    def validate_body_prefix(self, url: str, body_prefix: bytes) -> None:
        if not body_prefix:
            return
        if _has_forbidden_asset_prefix(body_prefix):
            raise UnsafeDownloadError(f"asset_body_type_not_allowed: {url}")
        if _is_text_asset_type(self._current_content_type) and _looks_like_binary_asset(body_prefix):
            raise UnsafeDownloadError(f"asset_body_binary: {url}")


def jsonable(value: Any) -> Any:
    """Return a JSON-serializable value without secret-bearing objects."""
    try:
        json.dumps(value)
    except TypeError:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): jsonable(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set):
            return [jsonable(item) for item in value]
        return str(value)
    return value


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_ndjson(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def artifact_ref(base: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def public_url(url: str) -> str:
    """Strip credentials and secret-like query parameters before persisting a URL."""
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return url.strip()
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    safe_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not SECRET_QUERY_RE.search(key)
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            host.lower(),
            parsed.path or "/",
            "",
            urlencode(safe_query, doseq=True),
            "",
        )
    )


def domain_from_input(value: str, *, email: str | None = None) -> str | None:
    if email:
        return domain_from_email(email)
    text = value.strip()
    if not text:
        return None
    if "@" in text and "://" not in text:
        return domain_from_email(text)
    parsed = urlparse(f"https://{text}") if "://" not in text else urlparse(text)
    host = (parsed.hostname or "").lower().rstrip(".")
    return host or None


def domain_from_email(email: str) -> str | None:
    match = re.fullmatch(r"[^@\s]+@([^@\s]+\.[^@\s]+)", email.strip())
    return match.group(1).lower().rstrip(".") if match else None


def is_free_or_disposable_email_domain(domain: str) -> bool:
    normalized = domain.lower().rstrip(".")
    return normalized in FREE_EMAIL_DOMAINS or normalized in DISPOSABLE_EMAIL_DOMAINS


def homepage_url_for_domain(domain: str) -> str:
    safe_domain = domain.strip().lower().rstrip(".")
    if not safe_domain or "/" in safe_domain or "://" in safe_domain:
        raise ContextPackError(f"Invalid domain: {domain}")
    return f"https://{safe_domain}/"


def same_policy_domain(url: str, base_domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return bool(host) and policy_domain_matches(host, base_domain)


def ensure_policy_for_domain(policy: PolicyConfig | None, domain: str) -> PolicyConfig:
    if policy is not None and policy.allowed_domains:
        return policy
    if policy is None:
        return PolicyConfig(allowed_domains=[domain])
    data = policy.model_dump(mode="json")
    data["allowed_domains"] = [domain]
    return PolicyConfig.model_validate(data)


async def fetch_pages(
    urls: list[str],
    *,
    run: ContextPackRun,
    max_pages: int = DEFAULT_CONTEXT_MAX_PAGES,
) -> list[PageSnapshot]:
    """Fetch bounded pages through the existing DocPull fetch pipeline."""
    selected: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        url = public_url(raw_url)
        if url in seen:
            continue
        allowed, reason = run.policy.allows_url(url)
        if not allowed:
            run.warn("policy_denied", f"Skipped URL denied by source policy: {url}", reason=reason)
            continue
        selected.append(url)
        seen.add(url)
        if len(selected) >= max_pages:
            break
    if not selected:
        return []

    config = DocpullConfig(url=selected[0], profile=ProfileName.CUSTOM)
    snapshots: list[PageSnapshot] = []
    async with Fetcher(config) as fetcher:
        for url in selected:
            ctx = await fetcher.fetch_one(url, save=False)
            run.http_request_count += 1
            if ctx.error:
                run.errors.append({"url": url, "error": ctx.error})
                continue
            if ctx.should_skip:
                run.warnings.append(
                    {"code": "page_skipped", "message": str(ctx.skip_reason or "skipped"), "url": url}
                )
                continue
            html = (ctx.html or b"").decode("utf-8", errors="replace")
            snapshots.append(
                PageSnapshot(
                    url=public_url(url),
                    title=ctx.title,
                    html=html,
                    markdown=ctx.markdown or "",
                    metadata=dict(ctx.metadata or {}),
                    extraction=dict(ctx.extraction_info or {}),
                    source_type=ctx.source_type,
                )
            )
    return snapshots


def fetch_pages_blocking(
    urls: list[str],
    *,
    run: ContextPackRun,
    max_pages: int = DEFAULT_CONTEXT_MAX_PAGES,
) -> list[PageSnapshot]:
    return asyncio.run(fetch_pages(urls, run=run, max_pages=max_pages))


def soup_for(page: PageSnapshot) -> BeautifulSoup:
    return BeautifulSoup(page.html or page.markdown, "html.parser")


def text_excerpt(text: str, needle: str | None = None, *, limit: int = 280) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    if needle:
        index = cleaned.lower().find(needle.lower())
        if index >= 0:
            start = max(0, index - 80)
            return cleaned[start : start + limit].strip()
    return cleaned[:limit].strip()


def citation_map_for_pages(pages: list[PageSnapshot]) -> dict[str, Any]:
    sources = [
        {
            "citation_id": f"S{index}",
            "url": page.url,
            "title": page.title or page.url,
        }
        for index, page in enumerate(pages, start=1)
    ]
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_count": len(sources),
        "sources": sources,
    }


def evidence_for_page(
    page: PageSnapshot,
    pages: list[PageSnapshot],
    *,
    field: str,
    excerpt: str | None = None,
) -> EvidenceRef:
    index = pages.index(page) + 1 if page in pages else 1
    return EvidenceRef(
        citation_id=f"S{index}",
        url=page.url,
        title=page.title,
        field=field,
        excerpt=excerpt or text_excerpt(page.markdown),
    )


def extract_links(page: PageSnapshot, *, base_url: str | None = None) -> list[dict[str, str]]:
    soup = soup_for(page)
    root = base_url or page.url
    links: list[dict[str, str]] = []
    for tag in soup.find_all("a"):
        href = str(tag.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        links.append(
            {
                "url": public_url(urljoin(root, href)),
                "text": " ".join(tag.get_text(" ").split()),
            }
        )
    return links


def likely_internal_pages(home: PageSnapshot, domain: str, *, max_pages: int) -> list[str]:
    keywords = (
        "about",
        "company",
        "contact",
        "press",
        "pricing",
        "products",
        "product",
        "solutions",
        "customers",
        "team",
        "brand",
    )
    urls = [home.url]
    for link in extract_links(home):
        url = link["url"]
        parsed_path = (urlparse(url).path or "/").lower()
        text = link["text"].lower()
        if not same_policy_domain(url, domain):
            continue
        if any(keyword in parsed_path or keyword in text for keyword in keywords):
            urls.append(url)
        if len(urls) >= max_pages:
            break
    return urls


def content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename_from_url(url: str, *, default_suffix: str = ".bin") -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name or parsed.hostname or "asset"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "asset"
    if "." not in stem and default_suffix:
        stem += default_suffix
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return f"{Path(stem).stem}-{digest}{Path(stem).suffix}"


def allowed_by_domains(url: str, allowed_domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return bool(host) and any(policy_domain_matches(host, domain) for domain in allowed_domains)


def _header_value(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _is_text_asset_type(content_type: str) -> bool:
    return content_type.startswith("text/") or content_type in {"application/json"}


def _looks_like_binary_asset(body_prefix: bytes) -> bool:
    if not body_prefix:
        return False
    if b"\x00" in body_prefix:
        return True
    sample = body_prefix[:1024]
    allowed_control_bytes = {9, 10, 12, 13}
    control_count = sum(1 for byte in sample if byte < 32 and byte not in allowed_control_bytes)
    return len(sample) >= 64 and control_count / len(sample) > 0.05


def _has_forbidden_asset_prefix(body_prefix: bytes) -> bool:
    for _name, signature, offset in FORBIDDEN_ASSET_SIGNATURES:
        end = offset + len(signature)
        if len(body_prefix) >= end and body_prefix[offset:end] == signature:
            return True
    return False


async def fetch_asset(
    url: str,
    *,
    output_dir: Path,
    source_url: str,
    kind: str,
    allowed_domains: list[str],
    allowed_content_types: set[str],
    max_bytes: int = DEFAULT_ASSET_MAX_BYTES,
    run: ContextPackRun | None = None,
) -> AssetRef:
    """Fetch a bounded public asset with redirect and MIME checks."""
    safe_url = public_url(url)
    if not allowed_by_domains(safe_url, allowed_domains):
        return AssetRef(safe_url, kind, source_url, status="rejected", warning="domain_not_allowed")

    try:
        response = await fetch_context_asset_bytes(
            safe_url,
            allowed_domains=allowed_domains,
            allowed_content_types=allowed_content_types,
            max_bytes=max_bytes,
        )
        if run is not None:
            run.http_request_count += 1
        if response.status_code >= 400:
            return AssetRef(
                response.url,
                kind,
                source_url,
                content_type=response.content_type or None,
                status="failed",
                warning=f"http_status_{response.status_code}",
            )
        if not _asset_body_matches_content_type(response.content_type, response.content):
            return AssetRef(
                response.url,
                kind,
                source_url,
                content_type=response.content_type or None,
                status="rejected",
                warning="content_type_body_mismatch",
            )
        suffix = _suffix_for_content_type(response.content_type)
        filename = safe_filename_from_url(response.url, default_suffix=suffix)
        asset_path = output_dir / filename
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(response.content)
        return AssetRef(
            response.url,
            kind,
            source_url,
            path=artifact_ref(output_dir.parent, asset_path),
            content_type=response.content_type or None,
            bytes=len(response.content),
            sha256=content_hash_bytes(response.content),
            status="downloaded",
        )
    except UnsafeDownloadError as err:
        return AssetRef(safe_url, kind, source_url, status="rejected", warning=str(err)[:200])
    except (OSError, TimeoutError, ValueError) as err:
        return AssetRef(safe_url, kind, source_url, status="failed", warning=str(err)[:200])
    except Exception as err:  # noqa: BLE001
        return AssetRef(safe_url, kind, source_url, status="failed", warning=str(err)[:200])


async def fetch_context_asset_bytes(
    url: str,
    *,
    allowed_domains: list[str],
    allowed_content_types: set[str],
    max_bytes: int = DEFAULT_ASSET_MAX_BYTES,
    timeout_seconds: float = 20.0,
) -> RemoteAssetResponse:
    """Fetch bounded CSS/image bytes through docpull's validated HTTP transport."""
    safe_url = public_url(url)
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(safe_url)
    if not validation.is_valid:
        raise UnsafeDownloadError(validation.rejection_reason or "asset_url_rejected")
    if not allowed_by_domains(safe_url, allowed_domains):
        raise UnsafeDownloadError("asset_domain_not_allowed")

    policy = ContextAssetDownloadPolicy(
        allowed_domains=allowed_domains,
        allowed_content_types=allowed_content_types,
    )
    client = AsyncHttpClient(
        rate_limiter=PerHostRateLimiter(default_delay=0.0, default_concurrent=2),
        max_retries=0,
        max_content_size=max_bytes,
        user_agent="docpull-context-pack (+https://github.com/raintree-technology/docpull)",
        default_timeout=timeout_seconds,
        url_validator=validator,
        require_pinned_dns=True,
        download_policy=policy,
    )
    async with client:
        response = await client.get(safe_url, timeout=timeout_seconds)

    final_url = public_url(response.url)
    if not allowed_by_domains(final_url, allowed_domains):
        raise UnsafeDownloadError("asset_redirect_domain_not_allowed")
    content_type = content_type_base(response.content_type)
    if response.status_code < 400 and (not content_type or content_type not in policy.allowed_content_types):
        raise UnsafeDownloadError("asset_content_type_not_allowed")
    return RemoteAssetResponse(
        url=final_url,
        status_code=response.status_code,
        content_type=content_type,
        content=response.content,
    )


def fetch_asset_blocking(
    url: str,
    *,
    output_dir: Path,
    source_url: str,
    kind: str,
    allowed_domains: list[str],
    allowed_content_types: set[str],
    max_bytes: int = DEFAULT_ASSET_MAX_BYTES,
    run: ContextPackRun | None = None,
) -> AssetRef:
    return asyncio.run(
        fetch_asset(
            url,
            output_dir=output_dir,
            source_url=source_url,
            kind=kind,
            allowed_domains=allowed_domains,
            allowed_content_types=allowed_content_types,
            max_bytes=max_bytes,
            run=run,
        )
    )


def _suffix_for_content_type(content_type: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "text/css": ".css",
        "application/json": ".json",
    }
    return mapping.get(content_type, ".bin")


def _asset_body_matches_content_type(content_type: str, body: bytes) -> bool:
    if not body:
        return True
    if _has_forbidden_asset_prefix(body[:8192]):
        return False
    if content_type == "image/png":
        return body.startswith(PNG_SIGNATURE)
    if content_type == "image/jpeg":
        return body.startswith(JPEG_SIGNATURE)
    if content_type == "image/gif":
        return any(body.startswith(signature) for signature in GIF_SIGNATURES)
    if content_type == "image/webp":
        return body.startswith(b"RIFF") and len(body) >= 12 and body[8:12] == b"WEBP"
    if content_type == "image/x-icon":
        return any(body.startswith(signature) for signature in ICO_SIGNATURES) or body.startswith(
            PNG_SIGNATURE
        )
    if content_type == "image/svg+xml":
        return _svg_asset_looks_safe(body)
    if _is_text_asset_type(content_type):
        return not _looks_like_binary_asset(body[:8192])
    return True


def _svg_asset_looks_safe(body: bytes) -> bool:
    prefix = body[:8192]
    lowered = prefix.lower()
    if not SVG_TAG_RE.match(prefix):
        return False
    return b"<script" not in lowered and b"javascript:" not in lowered and b"<foreignobject" not in lowered


def write_basic_pack_files(
    *,
    run: ContextPackRun,
    pages: list[PageSnapshot],
    result_filename: str,
    result_payload: dict[str, Any],
    markdown_filename: str,
    markdown_text: str,
    pack_filename: str,
    extra_artifacts: dict[str, str] | None = None,
    local_pack_records: bool = True,
) -> dict[str, Any]:
    """Write common result, report, policy, citations, pack metadata, and accounting."""
    output_dir = run.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    citations = citation_map_for_pages(pages)
    source_policy = run.policy.to_source_policy_payload(
        source=run.workflow,
        url=pages[0].url if pages else None,
        metadata={"workflow": run.workflow, "input": public_url(run.input_value)},
    )
    citations_path = output_dir / "citations.json"
    source_policy_path = output_dir / "source_policy.json"
    result_path = output_dir / result_filename
    markdown_path = output_dir / markdown_filename
    pack_path = output_dir / pack_filename
    agent_context_path = output_dir / "AGENT_CONTEXT.md"

    write_json(citations_path, citations)
    write_json(source_policy_path, source_policy)
    markdown_path.write_text(markdown_text.rstrip() + "\n", encoding="utf-8")
    agent_context_path.write_text(agent_context_markdown(run.workflow, markdown_filename), encoding="utf-8")

    artifacts = {
        "result": result_filename,
        "markdown": markdown_filename,
        "citations": "citations.json",
        "source_policy": "source_policy.json",
        "agent_context": "AGENT_CONTEXT.md",
        "pack_metadata": pack_filename,
    }
    if extra_artifacts:
        artifacts.update(extra_artifacts)
    if local_pack_records:
        records_path, manifest_path, sources_path = write_documents_pack(output_dir, pages, run.workflow)
        artifacts.update(
            {
                "documents_ndjson": artifact_ref(output_dir, records_path),
                "corpus_manifest": artifact_ref(output_dir, manifest_path),
                "sources": artifact_ref(output_dir, sources_path),
            }
        )
    artifacts["accounting"] = "run.accounting.json"

    result_payload = {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        **result_payload,
        "output_dir": str(output_dir),
        "artifacts": artifacts,
    }
    pack_payload = {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "provider": "local",
        "workflow": run.workflow,
        "status": result_payload.get("status", "completed"),
        "input": public_url(run.input_value),
        "summary": result_payload.get("summary", {}),
        "warnings": run.warnings,
        "errors": run.errors,
        "request_options": {
            "source_policy": source_policy,
            "replay_config": result_payload.get("replay_config", {}),
        },
        "artifacts": artifacts,
    }
    write_json(result_path, result_payload)
    write_json(pack_path, pack_payload)

    write_run_accounting(
        output_dir,
        RunAccounting(
            budget_limit_usd=run.policy.budget.maximum_paid_cost_usd,
            estimated_paid_cost_usd=0.0,
            http_request_count=run.http_request_count,
            cache_hit_count=run.cache_hit_count,
            route_steps=default_route_steps(),
            command=run.workflow,
            metadata={"input": public_url(run.input_value)},
        ),
    )
    return result_payload


def write_documents_pack(
    output_dir: Path,
    pages: list[PageSnapshot],
    workflow: str,
) -> tuple[Path, Path, Path]:
    records: list[DocumentRecord] = []
    sources: list[dict[str, Any]] = []
    run_identity = RunIdentity.from_config(
        DocpullConfig(
            url=pages[0].url if pages else "",
            profile=ProfileName.CUSTOM,
        )
    )
    source_dir = output_dir / "sources"
    source_dir.mkdir(exist_ok=True)
    for index, page in enumerate(pages, start=1):
        content = page.markdown or BeautifulSoup(page.html, "html.parser").get_text("\n")
        record = DocumentRecord.from_page(
            url=page.url,
            title=page.title,
            content=content,
            metadata=page.metadata,
            extraction={**page.extraction, "workflow": workflow},
            source_type=page.source_type or workflow,
            run_identity=run_identity,
        )
        records.append(record)
        source_path = source_dir / f"{index:03d}.md"
        source_path.write_text(content, encoding="utf-8")
        sources.append(
            {
                "index": index,
                "url": page.url,
                "title": page.title or page.url,
                "path": artifact_ref(output_dir, source_path),
            }
        )
    records_path = output_dir / "documents.ndjson"
    records_path.write_text(
        "".join(record.model_dump_json(exclude_none=True) + "\n" for record in records),
        encoding="utf-8",
    )
    manifest_path = output_dir / "corpus.manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "output_format": "ndjson",
            "document_count": len({record.document_id for record in records}),
            "record_count": len(records),
            "records": [
                {
                    "document_id": record.document_id,
                    "url": record.url,
                    "title": record.title,
                    "content_hash": record.content_hash,
                    "source_type": record.source_type,
                    "output_path": sources[index]["path"] if index < len(sources) else None,
                }
                for index, record in enumerate(records)
            ],
        },
    )
    sources_path = output_dir / "sources.md"
    lines = ["# Sources", ""]
    for source in sources:
        lines.append(f"- {source['index']}. [{source['title']}]({source['url']})")
    sources_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return records_path, manifest_path, sources_path


def agent_context_markdown(workflow: str, markdown_filename: str) -> str:
    return (
        f"# {workflow}\n\n"
        f"Load `{markdown_filename}` first, then inspect `citations.json`, "
        "`source_policy.json`, and the workflow result JSON for structured fields.\n"
    )


def maybe_copy_asset(path: Path, destination: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
    return artifact_ref(destination.parent.parent, destination)


def css_url_values(css_text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"url\(([^)]+)\)", css_text, flags=re.IGNORECASE):
        value = match.group(1).strip().strip("'\"")
        if value and not value.startswith("data:"):
            urls.append(public_url(urljoin(base_url, value)))
    return urls


def resolve_url(value: str, base_url: str) -> str:
    return public_url(urljoin(base_url, value.strip()))


def write_pack_citations_from_documents(output_dir: Path) -> None:
    """Best-effort citation map for packs that primarily write documents.ndjson."""
    try:
        citations = build_citation_map(output_dir)
    except Exception:  # noqa: BLE001
        return
    write_json(output_dir / "citations.json", citations)


def status_from_errors(errors: list[dict[str, Any]]) -> Literal["completed", "completed_with_errors"]:
    return "completed_with_errors" if errors else "completed"


def quote_markdown(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("|", "\\|").replace("\n", " ").strip()

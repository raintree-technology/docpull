"""Shared writer for typed v3 context-pack lanes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from ..accounting import RunAccounting, default_route_steps, write_run_accounting
from ..conversion.chunking import TokenCounter, chunk_markdown
from ..http.client import AsyncHttpClient
from ..http.rate_limiter import PerHostRateLimiter
from ..models.document import DocumentRecord
from ..output_contract import default_rights_state, validate_pack_contract
from ..pipeline.manifest import CorpusManifest
from ..policy import PolicyConfig
from ..security.robots import RobotsChecker
from ..security.url_validator import UrlValidator
from ..time_utils import parse_persisted_datetime, utc_now, utc_now_iso
from .common import ContextPackRun, _write_workflow_contract_files, artifact_ref, write_json

PrepareLevel = Literal["raw", "agent", "eval"]
OfficialSourceContract = Literal["arxiv_api", "crossref_api", "ncbi_eutils", "mediawiki_rest"]
_OFFICIAL_CONTRACT_HOSTS: dict[OfficialSourceContract, frozenset[str]] = {
    "arxiv_api": frozenset({"export.arxiv.org"}),
    "crossref_api": frozenset({"api.crossref.org"}),
    "ncbi_eutils": frozenset({"eutils.ncbi.nlm.nih.gov"}),
    "mediawiki_rest": frozenset(),
}
_MEDIAWIKI_REST_HOST_SUFFIXES = (
    ".wikipedia.org",
    ".wikimedia.org",
    ".wiktionary.org",
    ".wikibooks.org",
    ".wikiquote.org",
    ".wikivoyage.org",
    ".wikiversity.org",
    ".wikisource.org",
    ".wikinews.org",
)
_MEDIAWIKI_REST_HOSTS = {
    "www.mediawiki.org",
    "mediawiki.org",
    "meta.wikimedia.org",
    "commons.wikimedia.org",
}
_HTTP_CACHE_CONTEXT: ContextVar[TypedHttpCache | None] = ContextVar(
    "docpull_typed_http_cache",
    default=None,
)


@dataclass(frozen=True)
class TypedPackItem:
    """One typed source item that can be emitted as v3 records."""

    title: str
    url: str
    markdown: str
    source_type: str
    item_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)
    extraction: dict[str, Any] = field(default_factory=dict)
    route: dict[str, Any] = field(default_factory=dict)
    rights: dict[str, Any] | None = None
    public: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteText:
    """Text fetched through DocPull's safe HTTP path."""

    text: str
    url: str
    content_type: str
    status_code: int


def read_https_text(
    url: str,
    *,
    accept: str,
    max_bytes: int = 5_000_000,
    delay_seconds: float = 0.0,
    headers: Mapping[str, str] | None = None,
    source_contract: OfficialSourceContract | None = None,
) -> RemoteText:
    """Fetch HTTPS text with validation, robots, byte limits, and optional delay."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _read_https_text_async(
                url,
                accept=accept,
                max_bytes=max_bytes,
                delay_seconds=delay_seconds,
                headers=headers,
                source_contract=source_contract,
            )
        )
    raise ValueError("Typed pack remote sources cannot be fetched while an event loop is running.")


async def read_https_text_async(
    url: str,
    *,
    accept: str,
    max_bytes: int = 5_000_000,
    delay_seconds: float = 0.0,
    headers: Mapping[str, str] | None = None,
    source_contract: OfficialSourceContract | None = None,
) -> RemoteText:
    """Async variant of :func:`read_https_text` for SDK callers already in an event loop."""
    return await _read_https_text_async(
        url,
        accept=accept,
        max_bytes=max_bytes,
        delay_seconds=delay_seconds,
        headers=headers,
        source_contract=source_contract,
    )


async def _read_https_text_async(
    url: str,
    *,
    accept: str,
    max_bytes: int,
    delay_seconds: float,
    headers: Mapping[str, str] | None,
    source_contract: OfficialSourceContract | None,
) -> RemoteText:
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(url)
    if not validation.is_valid:
        raise ValueError(f"Remote source rejected: {validation.rejection_reason}")
    _validate_source_contract(url, source_contract)
    cache = _HTTP_CACHE_CONTEXT.get()
    request_headers = {"Accept": accept}
    if headers:
        request_headers.update(headers)
    cache_key_headers = {key: value for key, value in request_headers.items() if key.lower() == "accept"}
    if cache is not None:
        cached = cache.get(url, accept=accept, headers=cache_key_headers, source_contract=source_contract)
        if cached is not None:
            return cached
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    rate_limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)
    async with AsyncHttpClient(
        rate_limiter=rate_limiter,
        url_validator=validator,
        default_timeout=30.0,
        max_content_size=max_bytes,
    ) as client:
        if source_contract is None:
            robots = RobotsChecker(user_agent=client.user_agent, url_validator=validator)
            if not robots.is_allowed(url):
                raise ValueError(f"Robots.txt disallows or could not verify remote source: {url}")
        response = await client.get(url, headers=request_headers)
    if response.status_code >= 400:
        raise ValueError(f"Could not fetch remote source {url}: HTTP {response.status_code}")
    remote = RemoteText(
        text=_decode_response(response.content, response.content_type),
        url=response.url,
        content_type=response.content_type,
        status_code=response.status_code,
    )
    if cache is not None:
        cache.set(
            url,
            remote,
            accept=accept,
            headers=cache_key_headers,
            source_contract=source_contract,
        )
    return remote


@contextmanager
def typed_http_cache(cache_dir: Path | None, *, ttl_days: int | None = 7) -> Any:
    """Use a small typed-pack HTTP response cache within this context."""
    if cache_dir is None:
        yield
        return
    cache = TypedHttpCache(cache_dir, ttl_days=ttl_days)
    token = _HTTP_CACHE_CONTEXT.set(cache)
    try:
        yield
    finally:
        _HTTP_CACHE_CONTEXT.reset(token)


class TypedHttpCache:
    """Small response cache for typed-pack official API calls and metadata fetches."""

    def __init__(self, cache_dir: Path, *, ttl_days: int | None) -> None:
        self.cache_dir = cache_dir.expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = ttl_days

    def get(
        self,
        url: str,
        *,
        accept: str,
        headers: Mapping[str, str],
        source_contract: OfficialSourceContract | None,
    ) -> RemoteText | None:
        path = self._path(url, accept=accept, headers=headers, source_contract=source_contract)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = parse_persisted_datetime(str(payload.get("fetched_at") or ""))
            if self.ttl_days is not None and utc_now() - fetched_at > timedelta(days=self.ttl_days):
                return None
            return RemoteText(
                text=str(payload["text"]),
                url=str(payload["url"]),
                content_type=str(payload["content_type"]),
                status_code=int(payload["status_code"]),
            )
        except Exception:
            return None

    def set(
        self,
        url: str,
        response: RemoteText,
        *,
        accept: str,
        headers: Mapping[str, str],
        source_contract: OfficialSourceContract | None,
    ) -> None:
        path = self._path(url, accept=accept, headers=headers, source_contract=source_contract)
        payload = {
            "schema_version": 1,
            "fetched_at": utc_now_iso(),
            "url": response.url,
            "status_code": response.status_code,
            "content_type": response.content_type,
            "source_contract": source_contract,
            "text": response.text,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _path(
        self,
        url: str,
        *,
        accept: str,
        headers: Mapping[str, str],
        source_contract: OfficialSourceContract | None,
    ) -> Path:
        key = json.dumps(
            {
                "url": url,
                "accept": accept,
                "headers": dict(sorted(headers.items())),
                "source_contract": source_contract,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"


def write_typed_pack(
    *,
    workflow: str,
    output_format: str,
    output_dir: Path,
    items: list[TypedPackItem],
    pack_filename: str,
    index_filename: str,
    items_filename: str,
    summary_filename: str,
    index_payload: dict[str, Any],
    summary_markdown: str,
    result_summary: dict[str, Any],
    objective: str | None = None,
    chunk_tokens: int = 4000,
    extra_artifacts: dict[str, Path] | None = None,
    prepare_level: PrepareLevel = "raw",
) -> dict[str, Any]:
    """Write common typed-lane artifacts and optionally prepare the pack."""
    if not items:
        raise ValueError(f"{workflow} must emit at least one item.")
    if chunk_tokens <= 0:
        raise ValueError("--chunk-tokens must be greater than zero.")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    allowed_domains = sorted(
        {
            str(urlparse(item.url).hostname or "").lower().removeprefix("www.")
            for item in items
            if urlparse(item.url).scheme == "https" and urlparse(item.url).hostname
        }
    )
    policy = PolicyConfig(allowed_domains=allowed_domains)
    run = ContextPackRun(
        workflow=workflow,
        output_dir=output_dir,
        policy=policy,
        input_value=items[0].url,
    )
    sources_dir = output_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    documents_path = output_dir / "documents.ndjson"
    items_path = output_dir / items_filename
    index_path = output_dir / index_filename
    summary_path = output_dir / summary_filename
    manifest = CorpusManifest(output_dir, output_format=output_format)
    counter = TokenCounter()
    records: list[DocumentRecord] = []
    public_items: list[dict[str, Any]] = []

    with documents_path.open("w", encoding="utf-8") as ndjson:
        for source_index, item in enumerate(items, start=1):
            markdown = item.markdown.strip() + "\n"
            source_path = sources_dir / f"{source_index:03d}-{_slugify(item.title)}.md"
            source_path.write_text(markdown, encoding="utf-8")
            source_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            chunks = chunk_markdown(markdown, max_tokens=chunk_tokens, counter=counter)
            if not chunks:
                chunks = chunk_markdown(
                    f"# {item.title}\n\n{markdown}",
                    max_tokens=chunk_tokens,
                    counter=counter,
                )
            public_item = {
                "schema_version": 3,
                "item_id": f"{output_format}_item_{source_index:04d}",
                "item_citation_id": f"I{source_index}",
                "title": item.title,
                "url": item.url,
                "kind": item.item_kind,
                "source_path": artifact_ref(output_dir, source_path),
                **_drop_none(item.public),
            }
            public_items.append(public_item)
            for record_index, chunk in enumerate(chunks, start=1):
                route = {
                    "name": f"local-{output_format}-parse",
                    "output_format": output_format,
                    "workflow": workflow,
                    "item_kind": item.item_kind,
                    **item.route,
                }
                metadata = {
                    "item_id": public_item["item_id"],
                    "item_citation_id": public_item["item_citation_id"],
                    "item_kind": item.item_kind,
                    "source_document_hash": source_hash,
                    "source_path": artifact_ref(output_dir, source_path),
                    **item.metadata,
                }
                extraction = {
                    "workflow": workflow,
                    "parsed_at": utc_now_iso(),
                    **item.extraction,
                }
                record = DocumentRecord.from_page(
                    url=item.url,
                    title=item.title,
                    content=chunk.text,
                    metadata=_drop_none(metadata),
                    extraction=_drop_none(extraction),
                    source_type=item.source_type,
                    content_type="text/markdown",
                    mime_type="text/markdown",
                    route=_drop_none(route),
                    rights=_rights_state(item.rights),
                    source_citation_id=f"S{source_index}",
                    record_citation_id=f"S{source_index}.{record_index}",
                    chunk_index=chunk.index if len(chunks) > 1 else None,
                    chunk_heading=chunk.heading if len(chunks) > 1 else None,
                    token_count=chunk.token_count,
                )
                records.append(record)
                manifest.add_record(record, source_path)
                ndjson.write(
                    json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
                )
                ndjson.write("\n")

    manifest_path = manifest.finalize()
    _write_ndjson(items_path, public_items)
    index_payload = {
        "schema_version": 3,
        "generated_at": utc_now_iso(),
        "workflow": workflow,
        "output_format": output_format,
        "item_count": len(public_items),
        "record_count": len(records),
        **index_payload,
        "items": public_items,
    }
    write_json(index_path, index_payload)
    summary_path.write_text(summary_markdown.strip() + "\n", encoding="utf-8")

    artifacts: dict[str, str] = {
        "pack": pack_filename,
        "documents_ndjson": artifact_ref(output_dir, documents_path),
        "corpus_manifest": artifact_ref(output_dir, manifest_path),
        "sources": "sources.md",
        "acquisition_routes": "acquisition.routes.json",
        "index": artifact_ref(output_dir, index_path),
        "items": artifact_ref(output_dir, items_path),
        "markdown": artifact_ref(output_dir, summary_path),
        "source_policy": "source_policy.json",
        "accounting": "run.accounting.json",
        "workflow_request": "workflow.request.json",
        "workflow_result": "workflow.result.json",
        "artifact_manifest": "artifact.manifest.json",
    }
    if extra_artifacts:
        artifacts.update({key: artifact_ref(output_dir, value) for key, value in extra_artifacts.items()})

    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "workflow": workflow,
        "status": "completed",
        "input": {"sources": [item.url for item in items]},
        "output_dir": str(output_dir),
        "objective": objective or f"Review {workflow} context records",
        "summary": {
            "item_count": len(public_items),
            "record_count": len(records),
            "chunk_count": sum(1 for record in records if record.chunk_id),
            **result_summary,
        },
        "artifacts": artifacts,
        "warnings": [],
        "errors": [],
        "replay_config": {
            "chunk_tokens": chunk_tokens,
            "prepare_level": prepare_level,
        },
        "validation": validate_pack_contract(output_dir, level="raw"),
    }
    pack_path = output_dir / pack_filename
    write_json(pack_path, result)
    if prepare_level != "raw":
        from ..pack_tools import prepare_pack

        prepare_pack(output_dir, default_search=False, graph=False, eval_grade=prepare_level == "eval")
        result["prepared_level"] = prepare_level
        result["validation"] = validate_pack_contract(output_dir, level=prepare_level)
        result["status"] = (
            "completed" if result["validation"]["status"] == "pass" else "completed_with_validation_errors"
        )
    elif result["validation"]["status"] != "pass":
        result["status"] = "completed_with_validation_errors"
    write_json(pack_path, result)
    source_policy = policy.to_source_policy_payload(
        source=workflow,
        url=items[0].url,
        metadata={"workflow": workflow, "source_count": len(items)},
    )
    write_json(output_dir / artifacts["source_policy"], source_policy)
    accounting = RunAccounting(
        budget_limit_usd=policy.budget.maximum_paid_cost_usd,
        estimated_paid_cost_usd=0.0,
        http_request_count=sum(urlparse(item.url).scheme == "https" for item in items),
        route_steps=default_route_steps(),
        command=workflow,
    )
    accounting_payload = accounting.to_dict()
    write_run_accounting(output_dir, accounting)
    run.progress("artifacts", "completed", message="Wrote workflow artifacts")
    run.progress("run", "completed", message=f"Completed {workflow}")
    _write_workflow_contract_files(
        run=run,
        result_payload=result,
        source_policy=source_policy,
        artifacts=artifacts,
        accounting_payload=accounting_payload,
    )
    return result


def simple_summary_markdown(*, title: str, source: str, items: list[TypedPackItem]) -> str:
    lines = ["# " + title, "", f"Source: {source}", f"Items: {len(items)}", "", "## Items", ""]
    for index, item in enumerate(items, start=1):
        lines.append(f"- [I{index}] [{item.title}]({item.url})")
    return "\n".join(lines)


def _write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(_drop_none(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _rights_state(value: dict[str, Any] | None) -> dict[str, Any]:
    rights = default_rights_state()
    if not value:
        return rights
    custom_allowed = value.get("allowed_use")
    allowed_overrides = custom_allowed if isinstance(custom_allowed, dict) else {}
    rights.update({key: item for key, item in value.items() if key != "allowed_use"})
    rights["allowed_use"] = {**rights["allowed_use"], **allowed_overrides}
    return rights


def _validate_source_contract(url: str, source_contract: OfficialSourceContract | None) -> None:
    if source_contract is None:
        return
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if source_contract == "mediawiki_rest":
        host = parsed.netloc.lower()
        allowed = host in _MEDIAWIKI_REST_HOSTS or any(
            host.endswith(suffix) for suffix in _MEDIAWIKI_REST_HOST_SUFFIXES
        )
        if parsed.scheme == "https" and allowed and "/w/rest.php/v1/" in parsed.path:
            return
        raise ValueError(
            f"Source contract {source_contract!r} does not allow fetching {url}; "
            "expected a Wikimedia/MediaWiki REST endpoint under /w/rest.php/v1/."
        )
    allowed_hosts = _OFFICIAL_CONTRACT_HOSTS[source_contract]
    if parsed.scheme != "https" or parsed.netloc.lower() not in allowed_hosts:
        raise ValueError(
            f"Source contract {source_contract!r} does not allow fetching {url}; "
            f"allowed hosts: {', '.join(sorted(allowed_hosts))}"
        )


def _decode_response(body: bytes, content_type: str) -> str:
    encoding = "utf-8"
    for part in content_type.split(";"):
        stripped = part.strip()
        if stripped.lower().startswith("charset="):
            encoding = stripped.split("=", 1)[1].strip().strip("\"'") or encoding
            break
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value).strip("-").lower()
    return slug[:80].strip("-") or "item"


__all__ = [
    "PrepareLevel",
    "OfficialSourceContract",
    "RemoteText",
    "TypedPackItem",
    "read_https_text",
    "read_https_text_async",
    "simple_summary_markdown",
    "typed_http_cache",
    "write_typed_pack",
]

"""Portable, baseline-aware website snapshots for downstream intelligence systems."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ..contracts import (
    ArtifactEntry,
    ArtifactManifest,
    ArtifactReference,
    BudgetUsage,
    HashDigest,
    ReplayConfiguration,
    WebsiteSnapshot,
    WebsiteSnapshotBaseline,
    WebsiteSnapshotDocument,
    WebsiteSnapshotManifests,
    WebsiteSnapshotOptions,
    WorkflowFailure,
    WorkflowProgressEvent,
    WorkflowRequest,
    WorkflowResult,
    WorkflowWarning,
    bundled_schema_path,
    canonical_sha256,
    file_sha256,
    stable_id,
    verify_website_snapshot_hash,
)
from ..evidence import classify_source_authority
from ..policy import PolicyConfig
from ..time_utils import utc_now_iso
from .common import (
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    asset_allowed_domains_for_domain,
    domain_from_input,
    ensure_policy_for_domain,
    extract_links,
    fetch_asset_blocking,
    fetch_pages_blocking,
    homepage_url_for_domain,
    public_url,
    same_policy_domain,
    soup_for,
    write_json,
)

WEBSITE_WORKFLOW = "website-pack"
WEBSITE_SNAPSHOT_ENTRYPOINT = "website.snapshot.v1.json"
WEBSITE_SCHEMA_FILENAME = "website-snapshot.v1.schema.json"
DEFAULT_WEBSITE_OUTPUT_DIR = Path("packs/websites")
MAX_VISUALS = 8
KEY_VISUAL_ROLES = {"home", "product", "pricing", "trust"}
IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/x-icon"}


def build_website_pack(
    url_or_domain: str,
    *,
    output_dir: Path = DEFAULT_WEBSITE_OUTPUT_DIR,
    request: WorkflowRequest,
    max_pages: int = 50,
    max_depth: int = 3,
    raw_html: bool = True,
    key_page_visuals: bool = True,
    render_fallback: bool = True,
    pdf_enabled: bool = False,
    baseline_pack: Path | None = None,
    baseline_snapshot_id: str | None = None,
    baseline_snapshot_hash: str | None = None,
    entity_reference: str | None = None,
) -> dict[str, Any]:
    """Acquire a bounded website and emit one recursively verifiable portable-v3 pack."""

    domain = domain_from_input(url_or_domain)
    if not domain:
        raise ContextPackError("Could not resolve a domain from website-pack input.")
    policy = ensure_policy_for_domain(_request_policy(request), domain)
    output = output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run = ContextPackRun(
        workflow=WEBSITE_WORKFLOW,
        output_dir=output,
        policy=policy,
        input_value=url_or_domain,
    )
    entity = (entity_reference or domain).strip()
    captured_at = utc_now_iso()
    options = WebsiteSnapshotOptions(
        max_pages=max_pages,
        max_depth=max_depth,
        raw_html=raw_html,
        key_page_visuals=key_page_visuals,
        render_fallback=render_fallback,
        pdf_enabled=pdf_enabled,
    )
    if pdf_enabled:
        run.warn(
            "pdf_capture_unavailable",
            "PDF extraction was requested but is not part of the website.snapshot.v1 producer lane.",
        )

    baseline, baseline_payload = _load_verified_baseline(
        baseline_pack,
        expected_snapshot_id=baseline_snapshot_id,
        expected_snapshot_hash=baseline_snapshot_hash,
    )
    _prepare_conditional_cache(baseline_pack, output)
    run.fetch_output_dir = output / "acquired"
    run.cache_dir = output / "http-cache"
    start_url = public_url(url_or_domain if "://" in url_or_domain else homepage_url_for_domain(domain))
    baseline_urls = (
        [item.url for item in baseline.documents if item.state in {"added", "changed", "unchanged"}]
        if baseline
        else []
    )
    pages, failed_urls = _crawl_website(
        start_url,
        domain=domain,
        run=run,
        max_pages=max_pages,
        max_depth=max_depth,
        seed_urls=baseline_urls,
    )
    baseline_by_id = {item.document_id: item for item in baseline.documents} if baseline else {}

    document_rows: list[dict[str, Any]] = []
    snapshot_documents: list[WebsiteSnapshotDocument] = []
    produced_paths: set[str] = set()
    seen_document_ids: set[str] = set()
    visual_count = 0
    downloaded_assets: set[str] = set()

    for page in pages:
        canonical_url = _canonical_url(page.url)
        document_id = stable_id("doc", {"canonical_url": canonical_url})
        seen_document_ids.add(document_id)
        content = _page_content(page)
        content_hash = _sha256_text(content)
        document_version = content_hash
        previous = baseline_by_id.get(document_id)
        state = (
            "unchanged"
            if previous and previous.content_hash == content_hash
            else ("changed" if previous else "added")
        )
        page_role = _page_role(page)
        authority = classify_source_authority(
            canonical_url,
            official_domain=domain,
            declared_role=_authority_role(page_role),
        )
        okf_path = output / "okf" / f"{document_id}.md"
        okf_path.parent.mkdir(parents=True, exist_ok=True)
        okf_path.write_text(
            _okf_document(
                page=page,
                content=content,
                document_id=document_id,
                document_version=document_version,
                content_hash=content_hash,
                entity_reference=entity,
                page_role=page_role,
                authority=authority.model_dump(mode="json"),
            ),
            encoding="utf-8",
        )
        okf_ref = _reference(output, okf_path, media_type="text/markdown")
        produced_paths.add(okf_ref.path)

        raw_ref: ArtifactReference | None = None
        if raw_html:
            raw_path = output / "raw" / f"{document_id}.html"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(page.html, encoding="utf-8")
            raw_ref = _reference(output, raw_path, media_type="text/html")
            produced_paths.add(raw_ref.path)

        brand_assets = _capture_brand_assets(
            page,
            output=output,
            domain=domain,
            run=run,
            downloaded=downloaded_assets,
            enabled=page_role in KEY_VISUAL_ROLES,
        )
        produced_paths.update(item.path for item in brand_assets)

        screenshot_ref: ArtifactReference | None = None
        if key_page_visuals and page_role in KEY_VISUAL_ROLES and visual_count < MAX_VISUALS:
            screenshot_ref = _capture_screenshot(
                page,
                output=output,
                document_id=document_id,
                run=run,
            )
            if screenshot_ref:
                visual_count += 1
                produced_paths.add(screenshot_ref.path)

        document = WebsiteSnapshotDocument(
            document_id=document_id,
            document_version=document_version,
            content_hash=content_hash,
            url=page.url,
            canonical_url=canonical_url,
            title=page.title,
            entity_reference=entity,
            authority=authority,
            page_role=page_role,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            fetched_at=captured_at,
            okf=okf_ref,
            raw=raw_ref,
            screenshot=screenshot_ref,
            brand_assets=brand_assets,
        )
        snapshot_documents.append(document)
        document_rows.append(
            {
                "schema_version": 3,
                "document_id": document_id,
                "document_version": document_version,
                "url": page.url,
                "canonical_url": canonical_url,
                "title": page.title,
                "content": content,
                "content_hash": content_hash,
                "content_type": "text/markdown",
                "mime_type": "text/markdown",
                "fetched_at": captured_at,
                "source_type": WEBSITE_WORKFLOW,
                "entity_reference": entity,
                "authority": authority.model_dump(mode="json"),
                "page_role": page_role,
                "representations": {
                    "okf": okf_ref.model_dump(mode="json", exclude_none=True),
                    "raw": raw_ref.model_dump(mode="json", exclude_none=True) if raw_ref else None,
                    "screenshot": (
                        screenshot_ref.model_dump(mode="json", exclude_none=True) if screenshot_ref else None
                    ),
                    "brand_assets": [
                        item.model_dump(mode="json", exclude_none=True) for item in brand_assets
                    ],
                },
                "metadata": {"website_snapshot_state": state},
                "extraction": {"workflow": WEBSITE_WORKFLOW, "page_role": page_role},
                "route": {"name": "docpull.fetch"},
                "rights": {"status": "unknown"},
            }
        )

    failure_documents = _failure_documents(
        failed_urls,
        entity_reference=entity,
        domain=domain,
        captured_at=captured_at,
    )
    snapshot_documents.extend(failure_documents)
    seen_document_ids.update(item.document_id for item in failure_documents)
    if baseline and baseline_pack:
        baseline_rows = _baseline_document_rows(baseline_pack)
        for previous in baseline.documents:
            if (
                previous.document_id in seen_document_ids
                or _canonical_url(previous.url) not in run.unchanged_urls
                or previous.state not in {"added", "changed", "unchanged"}
            ):
                continue
            carried = _carry_baseline_document(
                previous,
                baseline_root=baseline_pack.resolve(),
                output=output,
                captured_at=captured_at,
            )
            snapshot_documents.append(carried)
            seen_document_ids.add(carried.document_id)
            for reference in [
                carried.okf,
                carried.raw,
                carried.screenshot,
                *carried.brand_assets,
            ]:
                if reference is not None:
                    produced_paths.add(reference.path)
            row = baseline_rows.get((carried.document_id, carried.document_version))
            if row:
                raw_metadata = row.get("metadata")
                metadata: dict[str, Any] = (
                    {str(key): value for key, value in raw_metadata.items()}
                    if isinstance(raw_metadata, dict)
                    else {}
                )
                document_rows.append(
                    {
                        **row,
                        "fetched_at": captured_at,
                        "metadata": {
                            **metadata,
                            "website_snapshot_state": "unchanged",
                            "conditional_reuse": True,
                        },
                    }
                )
            if carried.screenshot:
                visual_count += 1
    if baseline:
        for previous in baseline.documents:
            if previous.document_id in seen_document_ids:
                continue
            snapshot_documents.append(
                previous.model_copy(
                    update={
                        "state": "removed",
                        "okf": None,
                        "raw": None,
                        "screenshot": None,
                        "brand_assets": [],
                        "warnings": [*previous.warnings, "Not present in the current bounded crawl."],
                        "failure": None,
                    }
                )
            )

    documents_path = output / "documents.ndjson"
    documents_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in document_rows),
        encoding="utf-8",
    )
    produced_paths.add("documents.ndjson")
    for generated_dir in (output / "acquired", output / "http-cache"):
        if generated_dir.is_dir():
            produced_paths.update(
                str(path.relative_to(output)) for path in generated_dir.rglob("*") if path.is_file()
            )

    ordered_documents = sorted(snapshot_documents, key=lambda item: (item.url, item.document_id))
    okf_index_path = output / "okf" / "index.md"
    okf_index_path.parent.mkdir(parents=True, exist_ok=True)
    okf_index_path.write_text(_okf_index(ordered_documents), encoding="utf-8")
    produced_paths.add("okf/index.md")
    coverage = _coverage_payload(ordered_documents, run)
    corpus_path = output / "corpus.manifest.json"
    current_path = output / "current-run.manifest.json"
    coverage_path = output / "coverage.manifest.json"
    provenance_path = output / "provenance.manifest.json"
    write_json(corpus_path, _corpus_manifest(ordered_documents, captured_at))
    write_json(current_path, _current_run_manifest(ordered_documents, request, captured_at))
    write_json(coverage_path, coverage)
    write_json(provenance_path, _provenance_manifest(ordered_documents, entity, captured_at))
    source_policy_path = output / "source_policy.json"
    write_json(
        source_policy_path,
        policy.to_source_policy_payload(
            source=WEBSITE_WORKFLOW,
            url=start_url,
            metadata={"entity_reference": entity},
        ),
    )
    accounting_path = output / "run.accounting.json"
    write_json(
        accounting_path,
        {
            "schema_version": 1,
            "generated_at": captured_at,
            "budget_limit_usd": policy.budget.maximum_paid_cost_usd,
            "estimated_paid_cost_usd": 0.0,
            "actual_paid_cost_usd": 0.0,
            "paid_request_count": 0,
            "http_request_count": run.http_request_count,
            "cache_hit_count": run.cache_hit_count,
            "local_browser_seconds": 0.0,
            "blocked_actions": [],
        },
    )
    produced_paths.update(
        {
            "corpus.manifest.json",
            "current-run.manifest.json",
            "coverage.manifest.json",
            "provenance.manifest.json",
            "source_policy.json",
            "run.accounting.json",
        }
    )

    schema_path = bundled_schema_path(WEBSITE_SCHEMA_FILENAME)
    schema_sha256 = file_sha256(schema_path)
    pack_id = stable_id(
        "pack",
        {"request_id": request.request_id, "entity_reference": entity, "captured_at": captured_at},
    )
    run_id = stable_id(
        "run",
        {"request_id": request.request_id, "pack_id": pack_id, "captured_at": captured_at},
    )
    baseline_contract = (
        WebsiteSnapshotBaseline(
            snapshot_id=baseline.snapshot_id,
            snapshot_hash=baseline.snapshot_hash,
            verified=True,
        )
        if baseline
        else None
    )
    manifests = WebsiteSnapshotManifests(
        corpus=_reference(output, corpus_path, media_type="application/json"),
        current_run=_reference(output, current_path, media_type="application/json"),
        coverage=_reference(output, coverage_path, media_type="application/json"),
        provenance=_reference(output, provenance_path, media_type="application/json"),
        documents=_reference(output, documents_path, media_type="application/x-ndjson"),
    )
    warnings = _warning_models(run.warnings)
    snapshot_payload: dict[str, Any] = {
        "contract_version": "website.snapshot.v1",
        "schema_version": 1,
        "schema_sha256": schema_sha256,
        "pack_identity": {
            "pack_id": pack_id,
            "format": "portable-v3",
            "workflow": WEBSITE_WORKFLOW,
        },
        "run_identity": {
            "run_id": run_id,
            "request_id": request.request_id,
            "scheduler": None,
        },
        "entity_reference": entity,
        "captured_at": captured_at,
        "baseline": baseline_contract.model_dump(mode="json") if baseline_contract else None,
        "options": options.model_dump(mode="json"),
        "documents": [item.model_dump(mode="json", exclude_none=True) for item in ordered_documents],
        "coverage": coverage,
        "manifests": manifests.model_dump(mode="json", exclude_none=True),
        "visual_count": visual_count,
        "warnings": [item.model_dump(mode="json") for item in warnings],
    }
    snapshot_hash = canonical_sha256(snapshot_payload)
    snapshot = WebsiteSnapshot(
        snapshot_id=stable_id("snapshot", {"snapshot_hash": snapshot_hash}),
        snapshot_hash=snapshot_hash,
        **snapshot_payload,
    )
    if not verify_website_snapshot_hash(snapshot):
        raise ContextPackError("Internal website snapshot hash verification failed.")
    entrypoint_path = output / WEBSITE_SNAPSHOT_ENTRYPOINT
    serialized_snapshot = snapshot.model_dump(mode="json", exclude_none=True)
    serialized_snapshot.setdefault("baseline", None)
    write_json(entrypoint_path, serialized_snapshot)
    produced_paths.add(WEBSITE_SNAPSHOT_ENTRYPOINT)

    manifest = _write_artifact_manifest(
        output,
        produced_paths,
        pack_id=pack_id,
        run_id=run_id,
    )
    _write_workflow_result(
        output=output,
        request=request,
        run=run,
        snapshot=snapshot,
        artifact_manifest=manifest,
        baseline_payload=baseline_payload,
    )
    return serialized_snapshot


def validate_website_snapshot_pack(pack_dir: Path | str) -> WebsiteSnapshot:
    """Verify a website pack recursively while ignoring files outside its current manifest."""

    root = Path(pack_dir).expanduser().resolve()
    entrypoint = root / WEBSITE_SNAPSHOT_ENTRYPOINT
    manifest_path = root / "artifact.manifest.json"
    if not entrypoint.is_file() or not manifest_path.is_file():
        raise ContextPackError("Website pack is missing its entrypoint or artifact manifest.")
    try:
        snapshot = WebsiteSnapshot.model_validate_json(entrypoint.read_text(encoding="utf-8"))
        manifest = ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as err:
        raise ContextPackError(f"Website pack contract is invalid: {err}") from err
    if not verify_website_snapshot_hash(snapshot):
        raise ContextPackError("Website snapshot hash verification failed.")
    entries_by_path = {entry.path: entry for entry in manifest.entries}
    if len(entries_by_path) != len(manifest.entries):
        raise ContextPackError("Artifact manifest contains duplicate paths.")
    for entry in manifest.entries:
        path = _safe_pack_path(root, entry.path)
        if not path.is_file():
            raise ContextPackError(f"Artifact is missing: {entry.path}")
        if path.stat().st_size != entry.bytes or file_sha256(path) != entry.sha256:
            raise ContextPackError(f"Artifact verification failed: {entry.path}")
    aggregate = canonical_sha256(
        [entry.model_dump(mode="json", exclude_none=True) for entry in manifest.entries]
    )
    if aggregate != manifest.aggregate_sha256:
        raise ContextPackError("Artifact manifest aggregate hash verification failed.")
    if manifest.pack_id != str(snapshot.pack_identity.get("pack_id")):
        raise ContextPackError("Artifact manifest pack identity does not match the snapshot.")
    if manifest.run_id != str(snapshot.run_identity.get("run_id")):
        raise ContextPackError("Artifact manifest run identity does not match the snapshot.")
    references = [
        snapshot.manifests.corpus,
        snapshot.manifests.current_run,
        snapshot.manifests.coverage,
        snapshot.manifests.provenance,
        snapshot.manifests.documents,
        *[
            reference
            for document in snapshot.documents
            for reference in [
                document.okf,
                document.raw,
                document.screenshot,
                *document.brand_assets,
            ]
            if reference is not None
        ],
    ]
    for reference in references:
        manifest_entry = entries_by_path.get(reference.path)
        if manifest_entry is None or manifest_entry.sha256 != reference.sha256:
            raise ContextPackError(f"Snapshot reference is not pinned by the manifest: {reference.path}")
    return snapshot


def _crawl_website(
    start_url: str,
    *,
    domain: str,
    run: ContextPackRun,
    max_pages: int,
    max_depth: int,
    seed_urls: list[str] | None = None,
) -> tuple[list[PageSnapshot], list[dict[str, Any]]]:
    pages: list[PageSnapshot] = []
    failures: list[dict[str, Any]] = []
    initial = list(dict.fromkeys([start_url, *(seed_urls or [])]))[:max_pages]
    queued = set(initial)
    visited: set[str] = set()
    frontier = initial
    depth = 0
    while frontier and len(visited) < max_pages and depth <= max_depth:
        batch = [url for url in frontier if url not in visited][: max_pages - len(visited)]
        frontier = []
        before_errors = len(run.errors)
        acquired = fetch_pages_blocking(batch, run=run, max_pages=len(batch)) if batch else []
        visited.update(batch)
        pages.extend(acquired)
        failures.extend(run.errors[before_errors:])
        if depth >= max_depth:
            break
        candidates: list[tuple[int, str]] = []
        for page in acquired:
            for link in extract_links(page):
                url = _canonical_url(link["url"])
                if url in visited or url in queued or not same_policy_domain(url, domain):
                    continue
                score = _link_score(url, link.get("text", ""))
                candidates.append((score, url))
                queued.add(url)
        candidates.sort(key=lambda item: (-item[0], item[1]))
        frontier = [url for _, url in candidates[: max_pages - len(visited)]]
        depth += 1
    unique_pages: dict[str, PageSnapshot] = {}
    for page in pages:
        unique_pages.setdefault(_canonical_url(page.url), page)
    return list(unique_pages.values()), failures


def _link_score(url: str, text: str) -> int:
    value = f"{urlparse(url).path} {text}".lower()
    weighted = {
        "pricing": 12,
        "product": 10,
        "solution": 9,
        "docs": 8,
        "documentation": 8,
        "security": 8,
        "trust": 8,
        "changelog": 8,
        "release": 7,
        "privacy": 7,
        "terms": 7,
        "support": 6,
        "about": 5,
    }
    return sum(score for keyword, score in weighted.items() if keyword in value)


def _page_role(page: PageSnapshot) -> str:
    path = (urlparse(page.url).path or "/").lower()
    title = (page.title or "").lower()
    value = f"{path} {title}"
    if path in {"", "/"}:
        return "home"
    rules = (
        ("pricing", ("pricing", "plans", "billing")),
        ("documentation", ("/docs", "documentation", "api reference", "developer")),
        ("trust", ("security", "trust", "compliance", "soc 2", "subprocessor")),
        ("legal", ("privacy", "terms", "legal", "cookie", "dpa")),
        ("changelog", ("changelog", "release-notes", "release notes", "updates")),
        ("support", ("support", "help", "contact")),
        ("product", ("product", "products", "solution", "features", "platform")),
    )
    for role, keywords in rules:
        if any(keyword in value for keyword in keywords):
            return role
    return "other"


def _authority_role(page_role: str) -> str:
    if page_role == "legal" or page_role == "trust":
        return "legal"
    if page_role == "documentation":
        return "documentation"
    if page_role == "home":
        return "official_corporate"
    return "official_product"


def _page_content(page: PageSnapshot) -> str:
    content = page.markdown.strip()
    if not content:
        content = soup_for(page).get_text("\n").strip()
    return content + ("\n" if content else "")


def _okf_document(
    *,
    page: PageSnapshot,
    content: str,
    document_id: str,
    document_version: str,
    content_hash: str,
    entity_reference: str,
    page_role: str,
    authority: dict[str, Any],
) -> str:
    metadata = {
        "type": "Web Page",
        "title": page.title or page.url,
        "source": page.url,
        "document_id": document_id,
        "document_version": document_version,
        "content_hash": content_hash,
        "entity_reference": entity_reference,
        "page_role": page_role,
        "authority": authority,
    }
    frontmatter = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return f"---\njson: {frontmatter}\n---\n\n{content}"


def _okf_index(documents: list[WebsiteSnapshotDocument]) -> str:
    lines = ["# Website concepts", ""]
    for document in documents:
        if document.okf is None:
            continue
        lines.append(
            f"- [{document.title or document.url}]({Path(document.okf.path).name}) "
            f"— `{document.document_id}@{document.document_version}` ({document.page_role}, "
            f"{document.state})"
        )
    return "\n".join(lines).rstrip() + "\n"


def _prepare_conditional_cache(baseline_pack: Path | None, output: Path) -> None:
    if baseline_pack is None:
        return
    baseline_root = baseline_pack.resolve()
    for name in ("acquired", "http-cache"):
        source = baseline_root / name
        if source.is_dir():
            shutil.copytree(source, output / name, dirs_exist_ok=True)
    manifest_path = output / "http-cache" / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ContextPackError(f"Baseline conditional cache manifest is invalid: {err}") from err
    if not isinstance(manifest, dict):
        raise ContextPackError("Baseline conditional cache manifest must be an object.")
    for entry in manifest.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("file_path"), str):
            continue
        previous_path = Path(entry["file_path"])
        try:
            acquired_index = previous_path.parts.index("acquired")
        except ValueError:
            entry.pop("file_path", None)
            continue
        relative = Path(*previous_path.parts[acquired_index + 1 :])
        current_path = (output / "acquired" / relative).resolve()
        if current_path.is_file():
            entry["file_path"] = str(current_path)
        else:
            entry.pop("file_path", None)
    write_json(manifest_path, manifest)


def _baseline_document_rows(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    path = root.resolve() / "documents.ndjson"
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as err:
            raise ContextPackError(f"Baseline documents.ndjson is invalid: {err}") from err
        if not isinstance(item, dict):
            raise ContextPackError("Baseline documents.ndjson must contain objects.")
        document_id = item.get("document_id")
        document_version = item.get("document_version") or item.get("content_hash")
        if isinstance(document_id, str) and isinstance(document_version, str):
            rows[(document_id, document_version)] = item
    return rows


def _carry_baseline_document(
    document: WebsiteSnapshotDocument,
    *,
    baseline_root: Path,
    output: Path,
    captured_at: str,
) -> WebsiteSnapshotDocument:
    references = [document.okf, document.raw, document.screenshot, *document.brand_assets]
    for reference in references:
        if reference is None:
            continue
        source = _safe_pack_path(baseline_root, reference.path)
        destination = _safe_pack_path(output, reference.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if file_sha256(destination) != reference.sha256:
            raise ContextPackError(f"Carried baseline representation changed: {reference.path}")
    return document.model_copy(
        update={
            "state": "unchanged",
            "fetched_at": captured_at,
            "warnings": [*document.warnings, "Reused after conditional HTTP validation."],
            "failure": None,
        }
    )


def _candidate_asset_urls(page: PageSnapshot) -> list[str]:
    soup = soup_for(page)
    candidates: list[str] = []
    for tag in soup.find_all("link"):
        rel = " ".join(str(item).lower() for item in (tag.get("rel") or []))
        href = str(tag.get("href") or "").strip()
        if href and ("icon" in rel or "logo" in rel):
            candidates.append(public_url(urljoin(page.url, href)))
    for tag in soup.find_all("meta"):
        name = str(tag.get("property") or tag.get("name") or "").lower()
        content = str(tag.get("content") or "").strip()
        if content and name in {"og:image", "twitter:image"}:
            candidates.append(public_url(urljoin(page.url, content)))
    for tag in soup.find_all("img")[:20]:
        src = str(tag.get("src") or "").strip()
        descriptor = f"{tag.get('alt') or ''} {tag.get('class') or ''}".lower()
        if src and ("logo" in descriptor or "brand" in descriptor):
            candidates.append(public_url(urljoin(page.url, src)))
    return list(dict.fromkeys(candidates))


def _capture_brand_assets(
    page: PageSnapshot,
    *,
    output: Path,
    domain: str,
    run: ContextPackRun,
    downloaded: set[str],
    enabled: bool,
) -> list[ArtifactReference]:
    if not enabled:
        return []
    references: list[ArtifactReference] = []
    allowed_domains = asset_allowed_domains_for_domain(domain)
    for url in _candidate_asset_urls(page):
        if url in downloaded or len(downloaded) >= 12:
            continue
        downloaded.add(url)
        asset = fetch_asset_blocking(
            url,
            output_dir=output / "brand-assets",
            source_url=page.url,
            kind="brand_asset",
            allowed_domains=allowed_domains,
            allowed_content_types=IMAGE_MEDIA_TYPES,
            run=run,
        )
        if asset.status == "downloaded" and asset.path and asset.sha256:
            references.append(
                ArtifactReference(
                    path=asset.path,
                    sha256=asset.sha256,
                    bytes=asset.bytes,
                    media_type=asset.content_type,
                )
            )
        elif asset.warning:
            run.warn(
                "brand_asset_skipped",
                f"Could not include a selected brand asset from {page.url}.",
                reason=asset.warning,
            )
    return references


def _capture_screenshot(
    page: PageSnapshot,
    *,
    output: Path,
    document_id: str,
    run: ContextPackRun,
) -> ArtifactReference | None:
    if os.environ.get("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS") != "1":
        if not any(item.get("code") == "browser_capability_unavailable" for item in run.warnings):
            run.warn(
                "browser_capability_unavailable",
                "Key-page visuals were requested, but trusted browser rendering is not enabled.",
            )
        return None
    from .visuals import _run_screenshot_command

    path = output / "screenshots" / f"{document_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run_screenshot_command(
            binary=os.environ.get("DOCPULL_AGENT_BROWSER_BIN", "agent-browser"),
            url=page.url,
            output_path=path,
            viewport="1440x1000",
            full_page=True,
            wait_for="load",
        )
    except (ContextPackError, OSError) as err:
        run.warn(
            "browser_capture_failed",
            f"Could not capture key-page visual for {page.url}.",
            reason=str(err)[:300],
        )
        return None
    return _reference(output, path, media_type="image/png")


def _failure_documents(
    failures: list[dict[str, Any]],
    *,
    entity_reference: str,
    domain: str,
    captured_at: str,
) -> list[WebsiteSnapshotDocument]:
    output: list[WebsiteSnapshotDocument] = []
    seen: set[str] = set()
    empty_hash = _sha256_text("")
    for item in failures:
        raw_url = item.get("url") or item.get("source_url")
        if not isinstance(raw_url, str) or not raw_url or raw_url in seen:
            continue
        seen.add(raw_url)
        url = _canonical_url(raw_url)
        blocked = bool(item.get("blocked"))
        failure = WorkflowFailure(
            code=str(item.get("code") or "page_acquisition_failed"),
            message=str(item.get("error") or item.get("message") or "Page acquisition failed"),
            stage=str(item.get("stage") or "fetch"),
            retryable=not blocked and (item.get("http_status") in {408, 425, 429, 500, 502, 503, 504}),
            source_url=url,
            http_status=item.get("http_status") if isinstance(item.get("http_status"), int) else None,
            attempts=item.get("attempts") if isinstance(item.get("attempts"), int) else None,
            retry_after_seconds=(
                float(item["retry_after_seconds"])
                if isinstance(item.get("retry_after_seconds"), (int, float))
                else None
            ),
        )
        role = _page_role(PageSnapshot(url, None, "", "", {}, {}))
        output.append(
            WebsiteSnapshotDocument(
                document_id=stable_id("doc", {"canonical_url": url}),
                document_version=empty_hash,
                content_hash=empty_hash,
                url=url,
                canonical_url=url,
                entity_reference=entity_reference,
                authority=classify_source_authority(
                    url,
                    official_domain=domain,
                    declared_role=_authority_role(role),
                ),
                page_role=role,  # type: ignore[arg-type]
                state="blocked" if blocked else "failed",
                fetched_at=captured_at,
                failure=failure,
            )
        )
    return output


def _coverage_payload(
    documents: list[WebsiteSnapshotDocument],
    run: ContextPackRun,
) -> dict[str, Any]:
    states: dict[str, int] = {}
    roles: dict[str, int] = {}
    for document in documents:
        states[document.state] = states.get(document.state, 0) + 1
        roles[document.page_role] = roles.get(document.page_role, 0) + 1
    return {
        "schema_version": 1,
        "status": (
            "usable"
            if any(item.state in {"added", "changed", "unchanged"} for item in documents)
            else "failed"
        ),
        "document_count": len(documents),
        "active_document_count": sum(item.state in {"added", "changed", "unchanged"} for item in documents),
        "states": dict(sorted(states.items())),
        "page_roles": dict(sorted(roles.items())),
        "failure_count": len(run.errors),
        "warning_count": len(run.warnings),
    }


def _corpus_manifest(
    documents: list[WebsiteSnapshotDocument],
    captured_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "generated_at": captured_at,
        "output_format": "ndjson",
        "document_count": len(documents),
        "record_count": sum(item.state in {"added", "changed", "unchanged"} for item in documents),
        "records": [
            {
                "document_id": item.document_id,
                "document_version": item.document_version,
                "url": item.url,
                "content_hash": item.content_hash,
                "state": item.state,
                "page_role": item.page_role,
                "output_path": "documents.ndjson"
                if item.state in {"added", "changed", "unchanged"}
                else None,
            }
            for item in documents
        ],
    }


def _current_run_manifest(
    documents: list[WebsiteSnapshotDocument],
    request: WorkflowRequest,
    captured_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": captured_at,
        "request_id": request.request_id,
        "workflow": WEBSITE_WORKFLOW,
        "documents": [
            {
                "document_id": item.document_id,
                "document_version": item.document_version,
                "state": item.state,
            }
            for item in documents
        ],
    }


def _provenance_manifest(
    documents: list[WebsiteSnapshotDocument],
    entity_reference: str,
    captured_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": captured_at,
        "nodes": [
            {"id": entity_reference, "type": "entity"},
            *[
                {
                    "id": item.document_id,
                    "version": item.document_version,
                    "type": "document",
                    "url": item.url,
                    "authority": item.authority.model_dump(mode="json"),
                }
                for item in documents
            ],
        ],
        "edges": [
            {"from": item.document_id, "to": entity_reference, "type": "describes"} for item in documents
        ],
    }


def _load_verified_baseline(
    baseline_pack: Path | None,
    *,
    expected_snapshot_id: str | None,
    expected_snapshot_hash: str | None,
) -> tuple[WebsiteSnapshot | None, dict[str, Any] | None]:
    if baseline_pack is None:
        if expected_snapshot_id or expected_snapshot_hash:
            raise ContextPackError("Baseline identity was supplied without a baseline pack.")
        return None, None
    snapshot = validate_website_snapshot_pack(baseline_pack)
    if expected_snapshot_id and snapshot.snapshot_id != expected_snapshot_id:
        raise ContextPackError("Verified baseline snapshot_id does not match the request.")
    if expected_snapshot_hash and snapshot.snapshot_hash != expected_snapshot_hash.lower():
        raise ContextPackError("Verified baseline snapshot_hash does not match the request.")
    return snapshot, {
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "verified": True,
    }


def _write_artifact_manifest(
    output: Path,
    relative_paths: set[str],
    *,
    pack_id: str,
    run_id: str,
) -> ArtifactManifest:
    entries: list[ArtifactEntry] = []
    for relative in sorted(relative_paths):
        path = _safe_pack_path(output, relative)
        if not path.is_file():
            raise ContextPackError(f"Expected website artifact was not written: {relative}")
        entries.append(
            ArtifactEntry(
                name=_artifact_name(relative),
                path=relative,
                role=_artifact_role(relative),
                media_type=_media_type(path),
                bytes=path.stat().st_size,
                sha256=file_sha256(path),
            )
        )
    aggregate = canonical_sha256([entry.model_dump(mode="json", exclude_none=True) for entry in entries])
    manifest = ArtifactManifest(
        pack_id=pack_id,
        run_id=run_id,
        entries=entries,
        aggregate_sha256=aggregate,
    )
    write_json(output / "artifact.manifest.json", manifest.model_dump(mode="json", exclude_none=True))
    return manifest


def _write_workflow_result(
    *,
    output: Path,
    request: WorkflowRequest,
    run: ContextPackRun,
    snapshot: WebsiteSnapshot,
    artifact_manifest: ArtifactManifest,
    baseline_payload: dict[str, Any] | None,
) -> None:
    active_count = sum(item.state in {"added", "changed", "unchanged"} for item in snapshot.documents)
    failures = [item.failure for item in snapshot.documents if item.failure is not None]
    warnings = _warning_models(run.warnings)
    status = (
        "failed"
        if active_count == 0
        else ("completed_with_warnings" if failures or warnings else "completed")
    )
    finished_at = utc_now_iso()
    progress = [WorkflowProgressEvent.model_validate(item) for item in run.progress_events]
    result = WorkflowResult(
        request_id=request.request_id,
        workflow=WEBSITE_WORKFLOW,
        status=status,  # type: ignore[arg-type]
        started_at=run.started_at,
        finished_at=finished_at,
        pack_identity=snapshot.pack_identity,
        run_identity=snapshot.run_identity,
        summary={
            "usable_output": active_count > 0,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "document_count": len(snapshot.documents),
            "active_document_count": active_count,
            "baseline": baseline_payload,
        },
        data={"entrypoint": WEBSITE_SNAPSHOT_ENTRYPOINT},
        progress_events=progress,
        warnings=warnings,
        failures=failures,
        budget_usage=BudgetUsage(
            limit_usd=run.policy.budget.maximum_paid_cost_usd,
            estimated_usd=0.0,
            actual_usd=0.0,
            paid_request_count=0,
            http_request_count=run.http_request_count,
            cache_hit_count=run.cache_hit_count,
        ),
        hashes={
            "artifact_manifest": HashDigest(digest=file_sha256(output / "artifact.manifest.json")),
            "snapshot": HashDigest(digest=snapshot.snapshot_hash),
            "pack": HashDigest(digest=artifact_manifest.aggregate_sha256),
        },
        replay_configuration=ReplayConfiguration(
            browser_enabled=request.replay.browser_enabled,
            paid_routes_enabled=False,
            configuration=snapshot.options.model_dump(mode="json"),
        ),
        compatibility_artifacts={
            "website_snapshot": WEBSITE_SNAPSHOT_ENTRYPOINT,
            "documents": snapshot.manifests.documents.path,
        },
    )
    write_json(output / "workflow.result.json", result.model_dump(mode="json", exclude_none=True))


def _warning_models(items: list[dict[str, Any]]) -> list[WorkflowWarning]:
    warnings: list[WorkflowWarning] = []
    for item in items:
        raw_metadata = item.get("metadata")
        metadata = (
            {str(key): value for key, value in raw_metadata.items()} if isinstance(raw_metadata, dict) else {}
        )
        warnings.append(
            WorkflowWarning(
                code=str(item.get("code") or "warning"),
                message=str(item.get("message") or "Website snapshot warning"),
                metadata=metadata,
            )
        )
    return warnings


def _reference(output: Path, path: Path, *, media_type: str) -> ArtifactReference:
    relative = str(path.resolve().relative_to(output.resolve()))
    return ArtifactReference(
        path=relative,
        sha256=file_sha256(path),
        bytes=path.stat().st_size,
        media_type=media_type,
    )


def _safe_pack_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ContextPackError(f"Unsafe website artifact path: {relative}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as err:
        raise ContextPackError(f"Unsafe website artifact path: {relative}") from err
    return resolved


def _canonical_url(url: str) -> str:
    return public_url(url)


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact_name(relative: str) -> str:
    return relative.replace("/", "_").replace(".", "_")


def _artifact_role(relative: str) -> str:
    if relative == WEBSITE_SNAPSHOT_ENTRYPOINT or "manifest" in relative:
        return "manifest"
    if relative.startswith("okf/") or relative == "documents.ndjson":
        return "document"
    if relative.startswith("screenshots/"):
        return "visual"
    if relative.startswith("brand-assets/"):
        return "brand_asset"
    if relative.startswith("raw/"):
        return "raw_source"
    if relative == "source_policy.json":
        return "policy"
    if relative == "run.accounting.json":
        return "budget_usage"
    return "workflow_output"


def _media_type(path: Path) -> str | None:
    return {
        ".json": "application/json",
        ".ndjson": "application/x-ndjson",
        ".md": "text/markdown",
        ".html": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }.get(path.suffix.lower())


def _request_policy(request: WorkflowRequest) -> PolicyConfig | None:
    raw = request.options.get("policy")
    return PolicyConfig.model_validate(raw) if isinstance(raw, dict) else None


__all__ = [
    "DEFAULT_WEBSITE_OUTPUT_DIR",
    "WEBSITE_SNAPSHOT_ENTRYPOINT",
    "WEBSITE_WORKFLOW",
    "build_website_pack",
    "validate_website_snapshot_pack",
]

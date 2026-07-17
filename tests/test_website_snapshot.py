"""Golden contract and baseline-diff tests for website.snapshot.v1."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from docpull.context_packs import website as website_module
from docpull.context_packs.common import ContextPackError, ContextPackRun, PageSnapshot
from docpull.context_packs.website import (
    WEBSITE_SNAPSHOT_ENTRYPOINT,
    validate_website_snapshot_pack,
)
from docpull.contracts import WebsiteSnapshot
from docpull.workflows import create_workflow_request, run_workflow


def _page(
    url: str,
    *,
    title: str,
    body: str,
    links: list[tuple[str, str]] | None = None,
) -> PageSnapshot:
    anchors = "".join(f'<a href="{href}">{label}</a>' for href, label in (links or []))
    return PageSnapshot(
        url=url,
        title=title,
        html=f"<html><head><title>{title}</title></head><body>{anchors}<main>{body}</main></body></html>",
        markdown=f"# {title}\n\n{body}\n",
        metadata={},
        extraction={},
        source_type="html",
    )


def _fake_fetcher(site: dict[str, PageSnapshot], failures: dict[str, dict[str, Any]] | None = None):
    failure_map = failures or {}

    def fetch(
        urls: list[str],
        *,
        run: ContextPackRun,
        max_pages: int,
    ) -> list[PageSnapshot]:
        output: list[PageSnapshot] = []
        for url in urls[:max_pages]:
            run.http_request_count += 1
            if url in failure_map:
                run.errors.append({"url": url, **failure_map[url]})
            elif url in site:
                output.append(site[url])
        return output

    return fetch


def _run_snapshot(
    output: Path,
    *,
    options: dict[str, Any] | None = None,
) -> WebsiteSnapshot:
    request = create_workflow_request(
        "website-pack",
        "https://example.com/",
        output_dir=output,
        options={
            "max_pages": 10,
            "max_depth": 1,
            "raw_html": True,
            "key_page_visuals": True,
            **(options or {}),
        },
    )
    result = run_workflow(request)
    assert result["workflow"] == "website-pack"
    return WebsiteSnapshot.model_validate_json(
        (output / WEBSITE_SNAPSHOT_ENTRYPOINT).read_text(encoding="utf-8")
    )


def test_page_content_excludes_volatile_capture_time() -> None:
    def captured_page(captured_at: str) -> PageSnapshot:
        return PageSnapshot(
            url="https://example.com/",
            title="Example",
            html="<h1>Example</h1><p>Stable body.</p>",
            markdown=(
                f'---\ntitle: "Example"\ncrawled_at: "{captured_at}"\n---\n\n# Example\n\nStable body.\n'
            ),
            metadata={},
            extraction={},
        )

    first = captured_page("2026-07-17T05:00:00Z")
    second = captured_page("2026-07-17T06:00:00Z")

    first_content = website_module._page_content(first)
    second_content = website_module._page_content(second)

    assert first_content == second_content
    assert "crawled_at" not in first_content


def test_website_snapshot_is_recursive_portable_and_tamper_evident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", raising=False)
    site = {
        "https://example.com/": _page(
            "https://example.com/",
            title="Example",
            body="The official home page.",
            links=[("/pricing", "Pricing"), ("/product", "Product")],
        ),
        "https://example.com/pricing": _page(
            "https://example.com/pricing",
            title="Pricing",
            body="Pro costs $20 per month.",
        ),
        "https://example.com/product": _page(
            "https://example.com/product",
            title="Product",
            body="Automated competitive monitoring.",
        ),
    }
    monkeypatch.setattr(website_module, "fetch_pages_blocking", _fake_fetcher(site))
    output = tmp_path / "first"
    snapshot = _run_snapshot(output)

    assert snapshot.pack_identity["format"] == "portable-v3"
    assert {document.state for document in snapshot.documents} == {"added"}
    assert {document.page_role for document in snapshot.documents} >= {"home", "pricing", "product"}
    assert snapshot.visual_count == 0
    assert any(warning.code == "browser_capability_unavailable" for warning in snapshot.warnings)
    assert validate_website_snapshot_pack(output).snapshot_hash == snapshot.snapshot_hash
    assert (output / "okf" / "index.md").is_file()
    rows = [
        json.loads(line)
        for line in (output / "documents.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_identity = {(row["document_id"], row["document_version"]): row for row in rows}
    for document in snapshot.documents:
        row = by_identity[(document.document_id, document.document_version)]
        assert row["content_hash"] == document.content_hash
        assert document.document_id in (output / document.okf.path).read_text(  # type: ignore[union-attr]
            encoding="utf-8"
        )

    stale = output / "stale-from-prior-run.txt"
    stale.write_text("ignored", encoding="utf-8")
    assert validate_website_snapshot_pack(output).snapshot_hash == snapshot.snapshot_hash

    okf_path = output / snapshot.documents[0].okf.path  # type: ignore[union-attr]
    okf_path.write_text(okf_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    with pytest.raises(ContextPackError, match="verification failed"):
        validate_website_snapshot_pack(output)


def test_tampered_raw_ndjson_manifest_and_document_version_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", raising=False)
    site = {
        "https://example.com/": _page(
            "https://example.com/",
            title="Example",
            body="Evidence body.",
        )
    }
    monkeypatch.setattr(website_module, "fetch_pages_blocking", _fake_fetcher(site))
    source = tmp_path / "source"
    snapshot = _run_snapshot(source)
    mutations = {
        "raw": lambda root: (root / snapshot.documents[0].raw.path).write_text(  # type: ignore[union-attr]
            "tampered", encoding="utf-8"
        ),
        "ndjson": lambda root: (root / "documents.ndjson").write_text("{}\n", encoding="utf-8"),
        "manifest": lambda root: (root / "artifact.manifest.json").write_text(
            '{"contract_version":"artifact.manifest.v1"}', encoding="utf-8"
        ),
        "document-version": lambda root: _tamper_document_version(root),
    }
    for name, mutate in mutations.items():
        target = tmp_path / name
        shutil.copytree(source, target)
        mutate(target)
        with pytest.raises(ContextPackError):
            validate_website_snapshot_pack(target)


def _tamper_document_version(root: Path) -> None:
    path = root / WEBSITE_SNAPSHOT_ENTRYPOINT
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["documents"][0]["document_version"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_conditional_not_modified_reuses_verified_baseline_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", raising=False)
    site = {
        "https://example.com/": _page(
            "https://example.com/",
            title="Example",
            body="Stable home.",
            links=[("/pricing", "Pricing")],
        ),
        "https://example.com/pricing": _page(
            "https://example.com/pricing",
            title="Pricing",
            body="Pro $25 per month.",
        ),
    }
    monkeypatch.setattr(website_module, "fetch_pages_blocking", _fake_fetcher(site))
    baseline_dir = tmp_path / "baseline-conditional"
    baseline = _run_snapshot(baseline_dir)

    def not_modified(
        urls: list[str],
        *,
        run: ContextPackRun,
        max_pages: int,
    ) -> list[PageSnapshot]:
        for url in urls[:max_pages]:
            run.http_request_count += 1
            run.cache_hit_count += 1
            run.unchanged_urls.add(url)
        return []

    monkeypatch.setattr(website_module, "fetch_pages_blocking", not_modified)
    current_dir = tmp_path / "current-conditional"
    current = _run_snapshot(
        current_dir,
        options={
            "baseline_pack": str(baseline_dir),
            "baseline_snapshot_id": baseline.snapshot_id,
            "baseline_snapshot_hash": baseline.snapshot_hash,
        },
    )
    assert {document.state for document in current.documents} == {"unchanged"}
    rows = (current_dir / "documents.ndjson").read_text(encoding="utf-8")
    assert len([line for line in rows.splitlines() if line.strip()]) == 2
    assert validate_website_snapshot_pack(current_dir).snapshot_id == current.snapshot_id


def test_verified_baseline_emits_unchanged_changed_added_removed_and_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", raising=False)
    first_site = {
        "https://example.com/": _page(
            "https://example.com/",
            title="Example",
            body="Stable home copy.",
            links=[("/pricing", "Pricing"), ("/product", "Product")],
        ),
        "https://example.com/pricing": _page(
            "https://example.com/pricing",
            title="Pricing",
            body="Pro costs $20 per month.",
        ),
        "https://example.com/product": _page(
            "https://example.com/product",
            title="Product",
            body="Legacy feature page.",
        ),
    }
    monkeypatch.setattr(website_module, "fetch_pages_blocking", _fake_fetcher(first_site))
    baseline_dir = tmp_path / "baseline"
    baseline = _run_snapshot(baseline_dir)

    second_site = {
        "https://example.com/": _page(
            "https://example.com/",
            title="Example",
            body="Stable home copy.",
            links=[
                ("/pricing", "Pricing"),
                ("/new-product", "Product"),
                ("/trust", "Trust"),
            ],
        ),
        "https://example.com/pricing": _page(
            "https://example.com/pricing",
            title="Pricing",
            body="Pro costs $25 per month.",
        ),
        "https://example.com/new-product": _page(
            "https://example.com/new-product",
            title="New Product",
            body="A newly launched feature.",
        ),
    }
    failures = {
        "https://example.com/trust": {
            "error": "robots.txt denied acquisition",
            "code": "robots_disallowed",
            "stage": "fetch",
            "blocked": True,
            "attempts": 1,
        }
    }
    monkeypatch.setattr(
        website_module,
        "fetch_pages_blocking",
        _fake_fetcher(second_site, failures),
    )
    current_dir = tmp_path / "current"
    current = _run_snapshot(
        current_dir,
        options={
            "baseline_pack": str(baseline_dir),
            "baseline_snapshot_id": baseline.snapshot_id,
            "baseline_snapshot_hash": baseline.snapshot_hash,
        },
    )
    by_url = {document.url: document for document in current.documents}
    assert by_url["https://example.com/"].state == "unchanged"
    assert by_url["https://example.com/pricing"].state == "changed"
    assert by_url["https://example.com/new-product"].state == "added"
    assert by_url["https://example.com/product"].state == "removed"
    assert by_url["https://example.com/trust"].state == "blocked"
    assert by_url["https://example.com/trust"].failure is not None
    assert current.baseline is not None and current.baseline.verified is True
    assert validate_website_snapshot_pack(current_dir).snapshot_id == current.snapshot_id


def test_checked_in_golden_roles_cover_intelligence_surface() -> None:
    fixture = Path(__file__).parent / "fixtures" / "website_snapshot" / "golden-pages.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert set(payload["expected_roles"]) == {
        "home",
        "product",
        "pricing",
        "documentation",
        "trust",
        "legal",
        "changelog",
        "support",
        "other",
    }

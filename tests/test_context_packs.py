"""Tests for typed local context-pack workflows."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from docpull.context_packs._legacy_cli import (
    run_brand_pack_cli,
    run_extract_schema_cli,
    run_image_pack_cli,
    run_product_pack_cli,
    run_screenshot_pack_cli,
    run_search_pack_cli,
    run_styleguide_pack_cli,
)
from docpull.context_packs.brand import _local_firmographics, build_brand_pack
from docpull.context_packs.common import ContextPackError, PageSnapshot
from docpull.context_packs.product import build_product_pack
from docpull.context_packs.schema_extract import extract_schema
from docpull.context_packs.search import build_search_pack
from docpull.context_packs.styleguide import build_styleguide_pack
from docpull.context_packs.visuals import build_image_pack, capture_screenshot_pack
from tests.pack_fixtures import write_context_pack

pytestmark = pytest.mark.internal_legacy


class FakeFetcher:
    def __init__(self, _config: object) -> None:
        pass

    async def __aenter__(self) -> FakeFetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def fetch_one(self, url: str, *, save: bool) -> SimpleNamespace:
        assert save is False
        html = _html_for_url(url)
        return SimpleNamespace(
            error=None,
            should_skip=False,
            skip_reason=None,
            html=html.encode("utf-8"),
            markdown=_markdown_from_html(html),
            title="Acme",
            metadata={},
            extraction_info={},
            source_type="fake",
        )


def test_brand_pack_extracts_jsonld_org_and_socials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("docpull.context_packs.common.Fetcher", FakeFetcher)

    payload = build_brand_pack(
        "acme.test",
        output_dir=tmp_path / "brand",
        download_assets=False,
    )

    assert payload["brand"]["name"] == "Acme"
    assert payload["brand"]["description"] == "Acme makes local-first developer tools."
    assert payload["summary"]["social_link_count"] == 1
    assert payload["output_dir"] == str((tmp_path / "brand").resolve())
    assert payload["artifacts"]["accounting"] == "run.accounting.json"
    assert (tmp_path / "brand" / "brand.result.json").exists()
    assert (tmp_path / "brand" / "source_policy.json").exists()
    assert (tmp_path / "brand" / "run.accounting.json").exists()
    written = json.loads((tmp_path / "brand" / "brand.result.json").read_text(encoding="utf-8"))
    assert written["artifacts"]["pack_metadata"] == "brand.pack.json"


def test_brand_pack_rejects_free_email_by_default(tmp_path: Path) -> None:
    with pytest.raises(ContextPackError, match="Free or disposable"):
        build_brand_pack("ignored", email="person@gmail.com", output_dir=tmp_path / "brand")


def test_brand_firmographics_rejects_unrelated_founded_year() -> None:
    page = PageSnapshot(
        url="https://www.djangoproject.com/foundation/",
        title="Django Software Foundation",
        html=(
            "<html><body><p>Caktus Group is a Django consulting company founded in 2007.</p></body></html>"
        ),
        markdown="Caktus Group is a Django consulting company founded in 2007.",
        metadata={},
        extraction={},
        source_type="test",
    )

    payload = _local_firmographics(
        [page],
        profile_name="Django Project",
        domain="www.djangoproject.com",
    )

    assert payload == {}


def test_brand_firmographics_accepts_brand_founded_year() -> None:
    page = PageSnapshot(
        url="https://www.djangoproject.com/foundation/",
        title="Django Software Foundation",
        html="<html><body><p>The Django Software Foundation was founded in 2005.</p></body></html>",
        markdown="The Django Software Foundation was founded in 2005.",
        metadata={},
        extraction={},
        source_type="test",
    )

    payload = _local_firmographics(
        [page],
        profile_name="Django Project",
        domain="www.djangoproject.com",
    )

    assert payload["founded_year"]["value"] == 2005


def test_styleguide_pack_extracts_inline_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("docpull.context_packs.common.Fetcher", FakeFetcher)

    payload = build_styleguide_pack("https://acme.test", output_dir=tmp_path / "style")

    assert payload["summary"]["css_variable_count"] >= 2
    assert any(item["value"] == "#123456" for item in payload["tokens"]["colors"])
    assert (tmp_path / "style" / "tokens.css").exists()


def test_styleguide_pack_render_gate_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", raising=False)

    with pytest.raises(ContextPackError, match="requires DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS"):
        build_styleguide_pack("https://acme.test", output_dir=tmp_path / "style", render=True)


def test_product_pack_extracts_jsonld_product(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("docpull.context_packs.common.Fetcher", FakeFetcher)

    payload = build_product_pack("https://acme.test/pricing", output_dir=tmp_path / "products")

    assert payload["summary"]["is_product_page"] is True
    product = payload["products"][0]
    assert product["name"] == "Acme Pro"
    assert product["offers"][0]["price"] == 20.0
    assert (tmp_path / "products" / "products.ndjson").exists()


def test_extract_schema_uses_existing_pack_evidence(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["summary", "citations"],
                "properties": {
                    "summary": {"type": "string"},
                    "citations": {"type": "array"},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = extract_schema(pack, schema_path=schema, output_dir=tmp_path / "schema")

    assert payload["summary"]["validation_valid"] is True
    citation_url = urlparse(payload["data"]["citations"][0]["url"])
    assert citation_url.scheme == "https"
    assert citation_url.hostname == "docs.parallel.ai"
    basis_records = [
        json.loads(line)
        for line in (tmp_path / "schema" / "basis.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert basis_records
    assert all(record["schema_version"] == 2 for record in basis_records)
    assert any(record["claim_path"] == "data.summary" for record in basis_records)


def test_extract_schema_fills_common_product_fields_from_evidence(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(
        pack,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://books.example.com/item",
                "title": "A Light in the Attic",
                "content": "A Light in the Attic\nPrice (incl. tax) £51.77\nAvailability In stock",
                "content_hash": "hash_1",
                "source_type": "test",
            }
        ],
    )
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["title", "price", "currency", "availability"],
                "properties": {
                    "title": {"type": "string"},
                    "price": {"type": "string"},
                    "currency": {"type": "string"},
                    "availability": {"type": "string"},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = extract_schema(pack, schema_path=schema, output_dir=tmp_path / "schema")

    assert payload["summary"]["validation_valid"] is True
    assert payload["data"]["price"] == "£51.77"
    assert payload["data"]["currency"] == "GBP"
    assert payload["data"]["availability"] == "Availability In stock"
    assert "price" in payload["field_evidence"]
    basis_records = [
        json.loads(line)
        for line in (tmp_path / "schema" / "basis.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_path = {record["claim_path"]: record for record in basis_records}
    assert by_path["data.price"]["evidence_state"] == "supported"
    assert by_path["data.price"]["citation_ids"] == ["S1"]


def test_image_pack_extracts_markdown_images_from_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(
        pack,
        records=[
            {
                "document_id": "doc_1",
                "url": "https://assets.example.com/page",
                "title": "Assets",
                "content": "![Logo](https://assets.example.com/logo.png)",
                "content_hash": "hash_1",
                "source_type": "fixture",
            }
        ],
        include_domains=["assets.example.com"],
        provider="local",
    )

    payload = build_image_pack(pack, output_dir=tmp_path / "images", download_assets=False)

    assert payload["summary"]["candidate_count"] == 1
    assert payload["images"][0]["url"] == "https://assets.example.com/logo.png"


def test_search_pack_local_searches_existing_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)

    payload = build_search_pack("cited JSON", pack_dir=pack, output_dir=tmp_path / "search")

    assert payload["provider"] == "local"
    assert payload["replay_config"]["pack_dir"] == str(pack)
    assert payload["summary"]["result_count"] >= 1
    assert payload["artifacts"]["accounting"] == "run.accounting.json"
    written = json.loads((tmp_path / "search" / "search.pack.json").read_text(encoding="utf-8"))
    assert written["replay_config"]["provider"] == "local"
    assert (tmp_path / "search" / "search.results.ndjson").exists()
    assert (tmp_path / "search" / "run.accounting.json").exists()


def test_search_pack_provider_dry_run_writes_pack_artifacts(tmp_path: Path) -> None:
    payload = build_search_pack(
        "brand data",
        provider="tavily",
        output_dir=tmp_path / "search",
        dry_run=True,
    )

    assert payload["status"] == "dry_run"
    assert payload["output_dir"] == str((tmp_path / "search").resolve())
    assert payload["replay_config"]["provider"] == "tavily"
    assert payload["artifacts"]["source_policy"] == "source_policy.json"
    written = json.loads((tmp_path / "search" / "search.pack.json").read_text(encoding="utf-8"))
    assert written["replay_config"]["dry_run"] is True
    assert (tmp_path / "search" / "search.pack.json").exists()
    assert (tmp_path / "search" / "SEARCH.md").exists()
    assert (tmp_path / "search" / "run.accounting.json").exists()


def test_screenshot_pack_validates_options_before_renderer_gate(tmp_path: Path) -> None:
    with pytest.raises(ContextPackError, match="viewport"):
        capture_screenshot_pack("https://acme.test", output_dir=tmp_path / "shot", viewport="wide")

    with pytest.raises(ContextPackError, match="wait_for"):
        capture_screenshot_pack("https://acme.test", output_dir=tmp_path / "shot", wait_for="sleep")


def test_screenshot_pack_falls_back_to_agent_browser_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docpull.context_packs import visuals

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR4nGNgYGBgAAAABQABDQottAAAAABJRU5ErkJggg=="
    )
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append({"command": command, "kwargs": kwargs})
        if "--timeout" in command:
            return SimpleNamespace(
                returncode=1,
                stdout='{"error":"Unknown command: --timeout","success":false}',
                stderr="",
            )
        assert command == [
            "agent-browser",
            "--session",
            "docpull-screenshot-18a4abaa5dac",
            "batch",
            "--bail",
            "--json",
        ]
        batch = json.loads(str(kwargs["input"]))
        assert batch[0] == ["open", "https://acme.test/"]
        assert batch[1] == ["set", "viewport", "800", "600"]
        screenshot_path = Path(batch[-1][1])
        screenshot_path.write_bytes(png)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [{"command": step, "success": True, "result": {}} for step in batch[:-1]]
                + [{"command": batch[-1], "success": True, "result": {"path": str(screenshot_path)}}]
            ),
            stderr="",
        )

    monkeypatch.setenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", "1")
    monkeypatch.setattr(visuals.shutil, "which", lambda binary: f"/bin/{binary}")
    monkeypatch.setattr(visuals.subprocess, "run", fake_run)

    payload = capture_screenshot_pack(
        "https://acme.test",
        output_dir=tmp_path / "shot",
        viewport="800x600",
        agent_browser_binary="agent-browser",
    )

    assert payload["status"] == "completed"
    assert payload["screenshots"][0]["bytes"] == len(png)
    assert payload["screenshots"][0]["command"][3:6] == ["batch", "--bail", "--json"]
    assert (tmp_path / "shot" / "screenshots" / "page.png").read_bytes() == png
    assert len(calls) == 2


def test_screenshot_pack_reports_batch_timeout_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docpull.context_packs import visuals

    calls = 0

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                returncode=1,
                stdout='{"error":"Unknown command: --timeout","success":false}',
                stderr="",
            )
        raise subprocess.TimeoutExpired(command, timeout=45)

    monkeypatch.setenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", "1")
    monkeypatch.setattr(visuals.shutil, "which", lambda binary: f"/bin/{binary}")
    monkeypatch.setattr(visuals.subprocess, "run", fake_run)

    with pytest.raises(ContextPackError, match="timed out"):
        capture_screenshot_pack(
            "https://acme.test",
            output_dir=tmp_path / "shot",
            agent_browser_binary="agent-browser",
        )


@pytest.mark.parametrize(
    "argv",
    [
        ["brand-pack", "--help"],
        ["styleguide-pack", "--help"],
        ["product-pack", "--help"],
        ["extract-schema", "--help"],
        ["image-pack", "--help"],
        ["screenshot-pack", "--help"],
        ["search-pack", "--help"],
    ],
)
def test_context_pack_command_help_paths(argv: list[str]) -> None:
    runners = {
        "brand-pack": run_brand_pack_cli,
        "styleguide-pack": run_styleguide_pack_cli,
        "product-pack": run_product_pack_cli,
        "extract-schema": run_extract_schema_cli,
        "image-pack": run_image_pack_cli,
        "screenshot-pack": run_screenshot_pack_cli,
        "search-pack": run_search_pack_cli,
    }
    command, *rest = argv

    with pytest.raises(SystemExit) as exc_info:
        runners[command](rest)

    assert exc_info.value.code == 0


@pytest.mark.asyncio
async def test_mcp_dispatch_search_pack_local(tmp_path: Path) -> None:
    from docpull.mcp import server as mcp_server

    pack = tmp_path / "pack"
    write_context_pack(pack)

    result = await mcp_server._dispatch_tool(
        "search_pack",
        {
            "query": "cited JSON",
            "provider": "local",
            "pack_dir": str(pack),
            "output_dir": str(tmp_path / "search"),
        },
    )

    assert result.is_error is True
    assert result.text == "Unknown tool: search_pack"


@pytest.mark.asyncio
async def test_mcp_dispatch_brand_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docpull.mcp import server as mcp_server

    monkeypatch.setattr("docpull.context_packs.common.Fetcher", FakeFetcher)

    result = await mcp_server._dispatch_tool(
        "brand_pack",
        {
            "domain_or_url": "acme.test",
            "output_dir": str(tmp_path / "brand"),
            "download_assets": False,
        },
    )

    assert result.is_error is False
    assert result.data is not None
    assert result.data["contract_version"] == "workflow.result.v1"
    assert result.data["workflow"] == "brand-pack"
    assert result.data["status"] == "completed"


def _html_for_url(url: str) -> str:
    if "pricing" in url:
        return """
        <html><head>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product","name":"Acme Pro",
        "description":"A paid developer plan","offers":{"@type":"Offer","price":"20",
        "priceCurrency":"USD","url":"https://acme.test/pricing"}}
        </script></head><body><h1>Acme Pro</h1><p>$20/mo</p></body></html>
        """
    return """
    <html>
      <head>
        <meta name="theme-color" content="#123456">
        <style>
          :root { --brand-primary: #123456; --radius-card: 8px; }
          .button { color: #ffffff; background: var(--brand-primary); border-radius: 8px; }
          body { font-family: Inter, system-ui, sans-serif; margin: 16px; box-shadow: none; }
        </style>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Organization","name":"Acme",
        "description":"Acme makes local-first developer tools.","url":"https://acme.test",
        "sameAs":["https://github.com/acme"]}
        </script>
      </head>
      <body>
        <h1>Acme</h1>
        <h2>Local context for modern agents</h2>
        <a href="https://github.com/acme">GitHub</a>
        <button class="button">Start</button>
      </body>
    </html>
    """


def _markdown_from_html(html: str) -> str:
    return " ".join(html.replace("<", " <").replace(">", "> ").split())

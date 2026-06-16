"""Tests for filing-aware evidence packs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web

from docpull.evidence_pack import EvidencePackError, build_evidence_pack, load_evidence_rules
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidator


@pytest.fixture
async def filing_server(monkeypatch: pytest.MonkeyPatch):
    html = b"""<!doctype html><html><head><title>Acme 10-K</title></head>
<body>
<ix:hidden><div>HIDDEN XBRL SHOULD NOT SURVIVE</div></ix:hidden>
<article>
<h1>Item 1. Business</h1>
<p>Acme serves the U.S. government and Department of Defense through government contracts.</p>
<h2>Major Customers</h2>
<p>A major customer accounted for a material portion of revenue.</p>
<table><tr><td>Segment</td><td>Revenue</td></tr></table>
</article>
</body></html>"""

    async def page(_request: web.Request) -> web.Response:
        return web.Response(body=html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/filing.htm", page)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    def permissive_validate(self, hostname):  # type: ignore[no-untyped-def]
        from docpull.security.url_validator import UrlValidationResult

        return UrlValidationResult.valid()

    original_init = UrlValidator.__init__

    def init_with_http(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["allowed_schemes"] = {"http", "https"}
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "validate_hostname", permissive_validate)
    monkeypatch.setattr(UrlValidator, "__init__", init_with_http)
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, url: [])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, url: None)

    yield f"http://127.0.0.1:{port}/filing.htm"

    await runner.cleanup()


def test_load_evidence_rules_supports_yaml_literals_and_regex(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yml"
    rules_path.write_text(
        """
profile: vendor-dependency
categories:
  government_customer:
    patterns:
      - "U.S. government"
      - pattern: "Department of Defense"
  customer_concentration:
    patterns:
      - regex: 'major\\s+customer'
""",
        encoding="utf-8",
    )

    rules = load_evidence_rules(rules_path)

    assert rules.profile == "vendor-dependency"
    assert [pattern.category for pattern in rules.patterns] == [
        "government_customer",
        "government_customer",
        "customer_concentration",
    ]
    assert rules.patterns[0].method == "literal"
    assert rules.patterns[2].method == "regex"


def test_load_evidence_rules_accepts_checked_in_vendor_dependency_profile() -> None:
    rules = load_evidence_rules(Path("docs/examples/vendor-dependency-rules.yml"))

    assert rules.profile == "vendor-dependency"
    assert {pattern.category for pattern in rules.patterns} >= {
        "government_customer",
        "customer_concentration",
        "segment_revenue",
        "related_party",
    }


def test_load_evidence_rules_rejects_invalid_confidence(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yml"
    rules_path.write_text(
        """
profile: bad-rules
categories:
  government_customer:
    patterns:
      - pattern: "U.S. government"
        confidence: 2
""",
        encoding="utf-8",
    )

    with pytest.raises(EvidencePackError, match="Invalid confidence"):
        load_evidence_rules(rules_path)


@pytest.mark.asyncio
async def test_evidence_pack_writes_filing_outputs(
    filing_server: str,
    tmp_path: Path,
) -> None:
    filings_path = tmp_path / "filings.ndjson"
    filings_path.write_text(
        json.dumps(
            {
                "cik": "0000123456",
                "accession_number": "0000123456-26-000001",
                "form": "10-K",
                "filing_date": "2026-02-15",
                "issuer_name": "Acme Corp",
                "primary_document_url": filing_server,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rules_path = tmp_path / "vendor-dependency.yml"
    rules_path.write_text(
        """
profile: vendor-dependency
categories:
  government_customer:
    patterns:
      - "U.S. government"
  customer_concentration:
    patterns:
      - "major customer"
""",
        encoding="utf-8",
    )
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "corpus.manifest.json").write_text(
        json.dumps({"records": [{"url": filing_server, "source_document_hash": "old-hash"}]}),
        encoding="utf-8",
    )

    summary = await build_evidence_pack(
        filings_path=filings_path,
        rules_path=rules_path,
        output_dir=pack_dir,
        extractor="default",
        sec_user_agent="Acme evidence research contact@example.com",
    )

    assert summary["document_count"] == 1
    assert summary["evidence_count"] >= 2
    for filename in (
        "evidence.pack.json",
        "documents.ndjson",
        "evidence.ndjson",
        "diagnostics.ndjson",
        "sources.md",
        "corpus.manifest.json",
        "EVIDENCE_CONTEXT.md",
        "AGENT_CONTEXT.md",
    ):
        assert (pack_dir / filename).exists()

    document = json.loads((pack_dir / "documents.ndjson").read_text(encoding="utf-8").splitlines()[0])
    assert document["cik"] == "0000123456"
    assert document["accession_number"] == "0000123456-26-000001"
    assert document["form"] == "10-K"
    assert document["filing_date"] == "2026-02-15"
    assert document["issuer_name"] == "Acme Corp"
    assert document["primary_document_url"] == filing_server
    assert document["source_document_hash"]
    assert "HIDDEN XBRL SHOULD NOT SURVIVE" not in document["content"]

    evidence = [json.loads(line) for line in (pack_dir / "evidence.ndjson").read_text().splitlines()]
    assert {item["category"] for item in evidence} >= {"government_customer", "customer_concentration"}
    assert all(item["source_url"] == filing_server for item in evidence)
    assert all(item["chunk_id"].startswith("chunk_") for item in evidence)
    assert all(item["source_hash"] == document["source_document_hash"] for item in evidence)
    assert all("surrounding_context" in item for item in evidence)
    assert all("confidence" in item for item in evidence)

    diagnostics = [
        json.loads(line)["code"] for line in (pack_dir / "diagnostics.ndjson").read_text().splitlines()
    ]
    assert "source_hash_changed" in diagnostics

    context = (pack_dir / "EVIDENCE_CONTEXT.md").read_text(encoding="utf-8")
    agent_context = (pack_dir / "AGENT_CONTEXT.md").read_text(encoding="utf-8")
    assert "`evidence.ndjson`" in context
    assert "`documents.ndjson`" in context
    assert agent_context == context

    pack = json.loads((pack_dir / "evidence.pack.json").read_text(encoding="utf-8"))
    assert pack["workflow"] == "evidence-pack"
    assert pack["record_count"] == summary["record_count"]
    assert pack["artifacts"]["agent_context"] == "AGENT_CONTEXT.md"
    assert pack["sources"][0]["url"] == filing_server
    assert pack["sources"][0]["evidence_count"] >= 2

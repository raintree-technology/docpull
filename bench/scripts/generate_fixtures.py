"""Reproducibly generate the rights-safe v2 corpus, suites, and replay observations."""

# ruff: noqa: E501 -- source URLs are kept intact for review and reproducibility.

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "bench"
FIXTURES = BENCH / "fixtures" / "v2"
REPLAYS = BENCH / "replays" / "controlled-v2"
PAGES_BASE = "https://raintree-technology.github.io/docpull/bench-fixtures/v2"
RIGHTS = {
    "redistribution": "allowed",
    "source": "Repository-authored MIT benchmark fixture.",
}
FIXTURE_TIME = "2026-07-14T00:00:00+00:00"


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(content)


def _pdf(lines: list[str]) -> bytes:
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    commands = ["BT", "/F1 12 Tf", "72 740 Td"]
    for index, line in enumerate(escaped):
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _pdf_objects(objects)


def _pdf_objects(objects: list[bytes]) -> bytes:
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode() + item + b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(body)


def _encrypted_pdf() -> bytes:
    body = _pdf(["Encrypted PDF benchmark fixture"])
    return body.replace(b"/Root 1 0 R >>", b"/Root 1 0 R /Encrypt 99 0 R >>")


def _image_only_pdf() -> bytes:
    commands = b"q\n120 0 0 120 72 600 cm\n/Im0 Do\nQ"
    pixels = b"\x00\x33\x66"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(commands)).encode() + b" >>\nstream\n" + commands + b"\nendstream",
        (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length "
            + str(len(pixels)).encode()
            + b" >>\nstream\n"
            + pixels
            + b"\nendstream"
        ),
    ]
    return _pdf_objects(objects)


def _docx() -> bytes:
    import io

    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>DOCX Recovery Fixture</w:t></w:r></w:p>'
            "<w:p><w:r><w:t>alpha docx evidence</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>beta ordered conclusion</w:t></w:r></w:p>"
            "</w:body></w:document>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name].encode())
    return buffer.getvalue()


def _lifecycle_pack(pack_dir: Path, *, version: int) -> None:
    """Build raw v3 packs; benchmark runtime consumes them only through the public CLI."""
    from docpull.models.document import DocumentRecord
    from docpull.output_contract import write_raw_contract_sidecars

    if version == 1:
        records = [
            (
                "https://docs.example.test/auth",
                "Authentication",
                "Use short-lived bearer tokens. Never place credentials in generated artifacts.",
            ),
            (
                "https://docs.example.test/rate-limits",
                "Rate limits",
                "Retry HTTP 429 responses with exponential backoff and bounded jitter.",
            ),
            (
                "https://docs.example.test/deprecated",
                "Deprecated API",
                "The legacy endpoint remains available until the next major version.",
            ),
        ]
    else:
        records = [
            (
                "https://docs.example.test/auth",
                "Authentication",
                "Use short-lived bearer tokens. Never place credentials in generated artifacts.",
            ),
            (
                "https://docs.example.test/rate-limits",
                "Rate limits",
                "Retry HTTP 429 and 503 responses with exponential backoff, bounded jitter, and Retry-After.",
            ),
            (
                "https://docs.example.test/migration",
                "Migration guide",
                "Migrate from the legacy endpoint before the next major version.",
            ),
        ]
    pack_dir.mkdir(parents=True)
    (pack_dir / "sources").mkdir()
    documents = []
    for index, (url, title, content) in enumerate(records, 1):
        document = DocumentRecord.from_page(
            url=url,
            title=title,
            content=content,
            source_type="synthetic_fixture",
            route={
                "name": "synthetic-fixture",
                "status_code": 200,
                "bytes_downloaded": len(content),
            },
            rights={
                "status": "allowed",
                "allowed_use": {
                    "internal_indexing": "allowed",
                    "redistribution": "allowed",
                    "model_training": "allowed",
                    "eval_generation": "allowed",
                },
                "obligations": [],
                "basis": "benchmark_authored_synthetic_content",
            },
        ).model_copy(update={"fetched_at": FIXTURE_TIME})
        documents.append(document)
        _write(pack_dir / "sources" / f"{index:02d}.md", content + "\n")
    _write(
        pack_dir / "documents.ndjson",
        "".join(document.model_dump_json() + "\n" for document in documents),
    )
    manifest_records = [
        {
            "document_id": document.document_id,
            "url": document.url,
            "title": document.title,
            "content_hash": document.content_hash,
            "source_type": document.source_type,
            "output_path": f"sources/{index:02d}.md",
            "route": document.route,
            "rights": document.rights,
        }
        for index, document in enumerate(documents, 1)
    ]
    manifest = {
        "schema_version": 3,
        "output_contract_version": 3,
        "generated_at": FIXTURE_TIME,
        "document_count": len(documents),
        "record_count": len(documents),
        "records": manifest_records,
    }
    _write(
        pack_dir / "corpus.manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    write_raw_contract_sidecars(pack_dir, manifest_payload=manifest, output_format="ndjson")
    routes_path = pack_dir / "acquisition.routes.json"
    routes = json.loads(routes_path.read_text(encoding="utf-8"))
    routes["generated_at"] = FIXTURE_TIME
    _write(routes_path, json.dumps(routes, indent=2) + "\n")
    metadata = {
        "schema_version": 3,
        "provider": "synthetic-fixture",
        "workflow": "context-lifecycle-benchmark",
        "objective": "Keep an agent synchronized with a controlled API documentation corpus.",
        "request_options": {"source_policy": {"include_domains": ["docs.example.test"]}},
        "extract_error_count": 0,
        "record_count": len(documents),
        "sources": [
            {
                "index": index,
                "url": document.url,
                "title": document.title,
                "path": f"sources/{index:02d}.md",
            }
            for index, document in enumerate(documents, 1)
        ],
        "artifacts": {
            "documents_ndjson": "documents.ndjson",
            "corpus_manifest": "corpus.manifest.json",
            "sources": "sources.md",
            "acquisition_routes": "acquisition.routes.json",
        },
    }
    _write(pack_dir / "fixture.pack.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def generate_assets() -> None:
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    for index in range(1, 13):
        marker = f"extract-marker-{index:02d}"
        _write(
            FIXTURES / "extract" / f"{index:02d}.html",
            "<!doctype html><html><head>"
            f"<title>Controlled Extract {index:02d}</title></head><body>"
            "<nav>forbidden navigation boilerplate</nav><main>"
            f"<h1>Evidence Heading {index:02d}</h1><p>{marker} deterministic evidence "
            "with structural fidelity and a stable canonical URL.</p>"
            "<pre><code>const fixture = true;</code></pre></main></body></html>\n",
        )
    for graph in range(1, 7):
        for page in range(1, 4):
            links = "".join(f'<a href="{other}.html">page {other}</a>' for other in range(1, 4))
            _write(
                FIXTURES / "crawl" / f"graph-{graph:02d}" / f"{page}.html",
                f"<!doctype html><html><body><main><h1>Graph {graph:02d} Page {page}</h1>"
                f"<p>crawl-{graph:02d}-{page} bounded graph evidence unique page {page}</p>"
                f"{links}</main></body></html>\n",
            )
    parse = FIXTURES / "parse"
    _write(parse / "01-plain.txt", "Plain Heading\nalpha plain evidence\nbeta ordered conclusion\n")
    _write(parse / "02-markdown.md", "# Markdown Heading\n\nalpha markdown evidence\n\n`beta code`\n")
    _write(parse / "03-document.docx", _docx())
    _write(parse / "04-text.pdf", _pdf(["Text PDF Heading", "alpha pdf evidence", "beta ordered conclusion"]))
    _write(parse / "05-table.pdf", _pdf(["Table PDF", "Name | Value", "alpha | 731", "beta | 204"]))
    _write(parse / "06-code.txt", "Code Heading\n```python\nalpha = 731\n```\nbeta conclusion\n")
    _write(parse / "07-malformed.pdf", b"%PDF-1.4\nmalformed and deliberately truncated\n")
    _write(parse / "08-encrypted.pdf", _encrypted_pdf())
    _write(parse / "09-ocr-gated.pdf", _image_only_pdf())
    _write(parse / "10-unicode.md", "# Unicode Heading\n\nalpha café 東京 evidence\n\nbeta conclusion\n")
    for index in range(1, 11):
        root = FIXTURES / "packs" / f"pack-{index:02d}"
        _write(root / "pack.yaml", f"schema_version: 3\nname: fixture-pack-{index:02d}\n")
        _write(
            root / "records.ndjson", json.dumps({"id": f"record-{index:02d}", "text": "pack evidence"}) + "\n"
        )
        _write(root / "citations.json", json.dumps({"record": f"record-{index:02d}"}, sort_keys=True) + "\n")
    for index in range(1, 13):
        root = FIXTURES / "structured" / f"case-{index:02d}"
        _write(root / "source.md", f"# Product {index:02d}\n\nIdentifier: S-{index:03d}\nCount: {index}\n")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["identifier", "count"],
            "properties": {"identifier": {"type": "string"}, "count": {"type": "integer"}},
            "additionalProperties": False,
        }
        _write(root / "schema.json", json.dumps(schema, indent=2, sort_keys=True) + "\n")
    for index in range(1, 13):
        root = FIXTURES / "change" / f"case-{index:02d}"
        _write(root / "before.md", f"# State\n\nidentity-{index:02d}: before value\n")
        after = "cosmetic   whitespace" if index % 4 == 0 else f"after value {index:02d}"
        _write(root / "after.md", f"# State\n\nidentity-{index:02d}: {after}\n")
    corpus = FIXTURES / "corpus"
    for index in range(1, 81):
        _write(
            corpus / f"record-{index:03d}.md", f"# Record {index:03d}\n\nfrozen-token-{index:03d} evidence\n"
        )
    for index in range(1, 21):
        _write(
            FIXTURES / "research" / f"task-{index:02d}.md",
            f"# Research Evidence {index:02d}\n\nfield-{index:02d}: value-{index:02d}\n",
        )
    _write(FIXTURES / "policy" / "robots.txt", "User-agent: *\nDisallow: /blocked\n")
    _write(FIXTURES / "policy" / "redirect-target.html", "<main>safe redirect target</main>\n")
    _lifecycle_pack(FIXTURES / "lifecycle" / "fixture-v1", version=1)
    _lifecycle_pack(FIXTURES / "lifecycle" / "fixture-v2", version=2)


def _metadata(description: str, family: str, *, critical: bool = True, split: str = "dev") -> dict[str, Any]:
    return {
        "description": description,
        "split": split,
        "family": family,
        "product_area": "evaluation-lab",
        "critical": critical,
        "live": False,
        "tags": ["controlled", family],
        "rights": RIGHTS,
    }


def _case(
    case_id: str, lane: str, inputs: dict[str, Any], expected: dict[str, Any], family: str
) -> dict[str, Any]:
    return {
        "id": case_id,
        "input": {"case_id": case_id, "lane": lane, **inputs},
        "expected": {"lane": lane, **expected},
        "metadata": _metadata(f"Controlled {lane} case {case_id}.", family),
    }


def controlled_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index in range(1, 13):
        case_id = f"extract.fixture.{index:02d}"
        url = f"{PAGES_BASE}/extract/{index:02d}.html"
        cases.append(
            _case(
                case_id,
                "extract",
                {"url": url, "include_domains": ["raintree-technology.github.io"]},
                {
                    "minimum_records": 1,
                    "minimum_content_chars": 80,
                    "required_terms": [f"extract-marker-{index:02d}", "deterministic evidence"],
                    "forbidden_terms": ["forbidden navigation boilerplate"],
                    "required_urls": [url],
                    "allowed_domains": ["raintree-technology.github.io"],
                    "required_headings": [f"Evidence Heading {index:02d}"],
                },
                "hosted-fixture",
            )
        )
    for graph in range(1, 7):
        case_id = f"crawl.fixture.graph-{graph:02d}"
        root = f"{PAGES_BASE}/crawl/graph-{graph:02d}"
        urls = [f"{root}/{page}.html" for page in range(1, 4)]
        cases.append(
            _case(
                case_id,
                "crawl",
                {
                    "url": urls[0],
                    "include_domains": ["raintree-technology.github.io"],
                    "include_path_prefixes": [f"/docpull/bench-fixtures/v2/crawl/graph-{graph:02d}/"],
                    "max_pages": 3,
                    "max_depth": 1,
                },
                {
                    "minimum_records": 3,
                    "minimum_content_chars": 150,
                    "required_terms": [f"crawl-{graph:02d}-{page}" for page in range(1, 4)],
                    "required_urls": urls,
                    "allowed_domains": ["raintree-technology.github.io"],
                    "maximum_duplicate_rate": 0,
                },
                "controlled-graph",
            )
        )
    parse_files = [
        ("01-plain.txt", "completed", ["Plain Heading", "alpha plain evidence"]),
        ("02-markdown.md", "completed", ["Markdown Heading", "alpha markdown evidence"]),
        ("03-document.docx", "completed", ["DOCX Recovery Fixture", "alpha docx evidence"]),
        ("04-text.pdf", "completed", ["Text PDF Heading", "alpha pdf evidence"]),
        ("05-table.pdf", "completed", ["Table PDF", "alpha", "731"]),
        ("06-code.txt", "completed", ["Code Heading", "alpha = 731"]),
        ("07-malformed.pdf", "failed", []),
        ("08-encrypted.pdf", "failed", []),
        ("09-ocr-gated.pdf", "unsupported", []),
        ("10-unicode.md", "completed", ["Unicode Heading", "café", "東京"]),
    ]
    for index, (name, status, terms) in enumerate(parse_files, 1):
        cases.append(
            _case(
                f"parse.fixture.{index:02d}",
                "parse",
                {"path": f"bench/fixtures/v2/parse/{name}"},
                {
                    "minimum_records": 1 if status == "completed" else 0,
                    "minimum_content_chars": 10 if status == "completed" else 0,
                    "required_terms": terms,
                    "required_ordered_terms": terms,
                    "expected_status": status,
                },
                "document-parse",
            )
        )
    levels = ["raw", "agent", "eval"]
    for index in range(1, 11):
        level = levels[(index - 1) % len(levels)]
        cases.append(
            _case(
                f"pack.fixture.{index:02d}",
                "pack",
                {
                    "path": f"bench/fixtures/v2/packs/pack-{index:02d}",
                    "contract_level": level,
                    "action": "validate",
                },
                {
                    "minimum_records": 1,
                    "minimum_content_chars": 10,
                    "required_files": ["pack.yaml", "records.ndjson", "citations.json"],
                    "required_contract_level": level,
                    "minimum_stable_identities": 1,
                },
                "artifact-contract",
            )
        )
    for index in range(1, 13):
        cases.append(
            _case(
                f"structured.fixture.{index:02d}",
                "structured",
                {
                    "source_path": f"bench/fixtures/v2/structured/case-{index:02d}/source.md",
                    "schema_path": f"bench/fixtures/v2/structured/case-{index:02d}/schema.json",
                },
                {
                    "expected_value": {"identifier": f"S-{index:03d}", "count": index},
                    "required_evidence_ids": [f"structured-evidence-{index:02d}"],
                },
                "typed-extraction",
            )
        )
    lifecycle_checks = [
        "raw_contract",
        "eval_prepare",
        "stable_identity",
        "exact_diff",
        "offline_search",
        "exports",
        "context_ci",
        "lock_drift",
        "credential_non_persistence",
        "zero_budget",
    ]
    lifecycle_details: dict[str, dict[str, Any]] = {
        "raw_contract": {"status": "pass", "records": 3},
        "eval_prepare": {"status": "pass", "citation_entries": 3},
        "stable_identity": {"stable_documents": 3},
        "exact_diff": {"added": 1, "removed": 1, "changed": 1, "unchanged": 1},
        "offline_search": {"network": "disabled"},
        "exports": {"network": "disabled", "vector_records": 3},
        "context_ci": {"network": "disabled"},
        "lock_drift": {"drift_rejected": True, "network": "disabled"},
        "credential_non_persistence": {"secret_persisted": False, "network": "disabled"},
        "zero_budget": {"blocked": True, "budget_usd": 0.0},
    }
    for check in lifecycle_checks:
        cases.append(
            _case(
                f"lifecycle.{check}",
                "lifecycle",
                {"check": check, "timeout_seconds": 180},
                {"required_details": lifecycle_details[check]},
                "context-lifecycle",
            )
        )
    change_kinds = ["added", "removed", "changed", "cosmetic"]
    for index in range(1, 13):
        kind = change_kinds[(index - 1) % 4]
        cases.append(
            _case(
                f"change.fixture.{index:02d}",
                "change",
                {
                    "before_path": f"bench/fixtures/v2/change/case-{index:02d}/before.md",
                    "after_path": f"bench/fixtures/v2/change/case-{index:02d}/after.md",
                },
                {"events": [{"identity": f"identity-{index:02d}", "kind": kind, "category": "content"}]},
                "content-transition",
            )
        )
    for index in range(1, 101):
        answerable = index <= 80
        expected: dict[str, Any] = {
            "relevant_ids": [f"record-{index:03d}"] if answerable else [],
            "expected_empty": not answerable,
        }
        cases.append(
            _case(
                f"retrieval.query.{index:03d}",
                "retrieval",
                {
                    "pack_path": "bench/fixtures/v2/corpus",
                    "query": f"frozen-token-{index:03d}" if answerable else f"absent-token-{index:03d}",
                    "max_results": 5,
                },
                expected,
                "unanswerable" if not answerable else "frozen-pack",
            )
        )
    for index in range(1, 21):
        cases.append(
            _case(
                f"research.fixture.{index:02d}",
                "research",
                {
                    "corpus_path": f"bench/fixtures/v2/research/task-{index:02d}.md",
                    "question": f"Return field {index:02d} with evidence.",
                },
                {
                    "claims": [
                        {
                            "claim_id": f"claim-{index:02d}",
                            "value": f"value-{index:02d}",
                            "evidence_ids": [f"research-evidence-{index:02d}"],
                            "required_excerpt_terms": [f"field-{index:02d}", f"value-{index:02d}"],
                        }
                    ]
                },
                "fixed-corpus-evidence",
            )
        )
    scenarios = [
        "private_target",
        "robots",
        "zero_budget",
        "credential_leak",
        "rights",
        "redirect",
        "artifact_escape",
        "malformed_config",
    ]
    for index in range(1, 21):
        scenario = scenarios[(index - 1) % len(scenarios)]
        expected_status = "completed" if scenario == "credential_leak" else "failed"
        cases.append(
            _case(
                f"policy.{scenario}.{index:02d}",
                "policy",
                {
                    "scenario": scenario,
                    "target_url": "https://127.0.0.1/private" if scenario == "private_target" else None,
                    "fixture_path": "bench/fixtures/v2/policy/robots.txt",
                },
                {
                    "expected_status": expected_status,
                    "required_error_terms": []
                    if expected_status == "completed"
                    else [scenario.replace("_", " ")],
                    "maximum_request_count": 0,
                    "forbidden_output_terms": ["fixture-secret-value"],
                },
                "adversarial-policy",
            )
        )
    return cases


def _observation(case: dict[str, Any]) -> dict[str, Any]:
    lane = case["input"]["lane"]
    expected = case["expected"]
    base: dict[str, Any] = {
        "schema_version": 2,
        "case_id": case["id"],
        "system": "fixture",
        "status": "completed",
        "elapsed_seconds": 0.001,
        "cost_usd": 0,
        "cost_kind": "actual",
        "cost_basis": "Repository replay; no request.",
        "request_count": 0,
        "adapter_version": "2",
    }
    terms = "\n".join([*expected.get("required_terms", []), *expected.get("required_headings", [])])
    if lane in {"extract", "crawl", "parse"}:
        status = expected.get("expected_status", "completed")
        base["status"] = status
        if status != "completed":
            if status == "failed":
                base["error"] = "deterministic parse failure"
            return base
        urls = expected.get("required_urls") or [f"file:///{case['id']}"]
        per_record = max(1, expected.get("minimum_content_chars", 0) // len(urls) + 1)
        records = []
        for index, url in enumerate(urls):
            content = terms if index == 0 else f"unique record {index}"
            content += " x" * max(0, per_record - len(content))
            records.append({"url": url, "title": case["id"], "content": content, "metadata": {}})
        base["payload"] = {"kind": "content", "records": records, "selected_urls": urls}
    elif lane == "pack":
        base["payload"] = {
            "kind": "pack",
            "records": [{"url": "fixture://pack", "content": "pack evidence content", "metadata": {}}],
            "files": expected["required_files"],
            "contract_level": expected["required_contract_level"],
            "stable_identities": ["stable-identity"],
        }
    elif lane == "structured":
        base["payload"] = {
            "kind": "structured",
            "value": expected["expected_value"],
            "schema_valid": True,
            "evidence_ids": expected["required_evidence_ids"],
        }
    elif lane == "lifecycle":
        base["payload"] = {"kind": "checks", "details": expected["required_details"]}
    elif lane == "change":
        base["payload"] = {"kind": "changes", "events": expected["events"], "delay_seconds": 0.01}
    elif lane == "retrieval":
        results = [
            {"identity": identity, "title": identity, "excerpt": identity, "score": 1.0}
            for identity in expected["relevant_ids"]
        ]
        base["payload"] = {"kind": "retrieval", "results": results, "index_bytes": 4096}
    elif lane == "research":
        base["payload"] = {
            "kind": "research",
            "claims": [
                {
                    "claim_id": claim["claim_id"],
                    "value": claim["value"],
                    "evidence_ids": claim["evidence_ids"],
                    "excerpts": [" ".join(claim["required_excerpt_terms"])],
                }
                for claim in expected["claims"]
            ],
        }
    elif lane == "policy":
        base["status"] = expected["expected_status"]
        if base["status"] == "completed":
            base["payload"] = {"kind": "checks", "details": {"safe": True}}
        elif base["status"] == "failed":
            base["error"] = " ".join(expected["required_error_terms"]) or "policy failure"
    else:
        raise AssertionError(lane)
    return base


def _manifest() -> dict[str, Any]:
    files = []
    for path in sorted(FIXTURES.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(FIXTURES.parent).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "rights": "repository-authored-mit",
                }
            )
    return {"schema_version": 1, "generator": "bench/scripts/generate_fixtures.py", "files": files}


def _write_suite(
    path: Path, name: str, description: str, cases: list[dict[str, Any]], manifest_hash: str | None
) -> None:
    payload: dict[str, Any] = {
        "schema_version": 2,
        "name": name,
        "version": "2.0.0",
        "description": description,
        "cases": cases,
    }
    if manifest_hash:
        payload["fixture_manifest_sha256"] = manifest_hash
    _write(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def generate_suites() -> None:
    manifest = _manifest()
    manifest_path = BENCH / "fixtures" / "manifest.json"
    _write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    cases = controlled_cases()
    _write_suite(
        BENCH / "cases" / "controlled-v2.yaml",
        "docpull-controlled-evaluation",
        "Deterministic rights-safe controlled corpus covering ten non-live capability lanes.",
        cases,
        manifest_hash,
    )
    lifecycle = [case for case in cases if case["input"]["lane"] == "lifecycle"]
    _write_suite(
        BENCH / "cases" / "lifecycle-v2.yaml",
        "docpull-context-lifecycle",
        "Unified runner alias for the ten controlled lifecycle checks.",
        lifecycle,
        manifest_hash,
    )
    if REPLAYS.exists():
        shutil.rmtree(REPLAYS)
    REPLAYS.mkdir(parents=True)
    for case in cases:
        _write(
            REPLAYS / f"{case['id']}.json", json.dumps(_observation(case), indent=2, sort_keys=True) + "\n"
        )
    _write_live_search_suite()
    _migrate_v1_suites()
    _write_legacy_replays()


def _write_live_search_suite() -> None:
    entries = [
        (
            "technical",
            "Python asyncio TaskGroup reference",
            "docs.python.org",
            "https://docs.python.org/3/library/asyncio-task.html",
            "TaskGroup",
        ),
        (
            "technical",
            "Rust standard library Vec struct",
            "doc.rust-lang.org",
            "https://doc.rust-lang.org/std/vec/struct.Vec.html",
            "Vec<T>",
        ),
        (
            "technical",
            "Kubernetes liveness readiness startup probes",
            "kubernetes.io",
            "https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/",
            "startupProbe",
        ),
        (
            "technical",
            "PostgreSQL JSON functions jsonb_path_query",
            "www.postgresql.org",
            "https://www.postgresql.org/docs/current/functions-json.html",
            "jsonb_path_query",
        ),
        (
            "technical",
            "MDN AbortController API",
            "developer.mozilla.org",
            "https://developer.mozilla.org/en-US/docs/Web/API/AbortController",
            "AbortController",
        ),
        (
            "technical",
            "Node.js file system promises API",
            "nodejs.org",
            "https://nodejs.org/api/fs.html",
            "fsPromises",
        ),
        (
            "standards",
            "RFC 9110 HTTP semantics",
            "www.rfc-editor.org",
            "https://www.rfc-editor.org/rfc/rfc9110.html",
            "RFC 9110",
        ),
        (
            "standards",
            "RFC 3986 URI generic syntax",
            "www.rfc-editor.org",
            "https://www.rfc-editor.org/rfc/rfc3986.html",
            "RFC 3986",
        ),
        (
            "standards",
            "W3C WCAG 2.2 recommendation",
            "www.w3.org",
            "https://www.w3.org/TR/WCAG22/",
            "WCAG 2.2",
        ),
        (
            "standards",
            "WHATWG URL living standard",
            "url.spec.whatwg.org",
            "https://url.spec.whatwg.org/",
            "URL Standard",
        ),
        (
            "standards",
            "NIST FIPS 180-4 secure hash standard",
            "csrc.nist.gov",
            "https://csrc.nist.gov/pubs/fips/180-4/upd1/final",
            "FIPS 180-4",
        ),
        (
            "standards",
            "OpenAPI Specification 3.1.1",
            "spec.openapis.org",
            "https://spec.openapis.org/oas/v3.1.1.html",
            "3.1.1",
        ),
        (
            "security",
            "NVD CVE-2021-44228",
            "nvd.nist.gov",
            "https://nvd.nist.gov/vuln/detail/CVE-2021-44228",
            "CVE-2021-44228",
        ),
        (
            "security",
            "NVD CVE-2024-3094",
            "nvd.nist.gov",
            "https://nvd.nist.gov/vuln/detail/CVE-2024-3094",
            "CVE-2024-3094",
        ),
        (
            "security",
            "OWASP SSRF prevention cheat sheet",
            "cheatsheetseries.owasp.org",
            "https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html",
            "SSRF",
        ),
        (
            "security",
            "CISA known exploited vulnerabilities catalog",
            "www.cisa.gov",
            "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
            "Known Exploited Vulnerabilities",
        ),
        (
            "security",
            "GitHub advisory database GHSA-jfh8-c2jp-5v3q",
            "github.com",
            "https://github.com/advisories/GHSA-jfh8-c2jp-5v3q",
            "GHSA-jfh8-c2jp-5v3q",
        ),
        (
            "security",
            "Python security response process",
            "www.python.org",
            "https://www.python.org/dev/security/",
            "Python Security Response Team",
        ),
        (
            "product",
            "GitHub Actions workflow syntax",
            "docs.github.com",
            "https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax",
            "jobs.<job_id>",
        ),
        (
            "product",
            "Stripe API idempotent requests",
            "docs.stripe.com",
            "https://docs.stripe.com/api/idempotent_requests",
            "Idempotency-Key",
        ),
        (
            "product",
            "Cloudflare Workers platform limits",
            "developers.cloudflare.com",
            "https://developers.cloudflare.com/workers/platform/limits/",
            "CPU time",
        ),
        (
            "product",
            "Vercel Functions duration limits",
            "vercel.com",
            "https://vercel.com/docs/functions/limitations",
            "maxDuration",
        ),
        (
            "product",
            "Docker Compose specification",
            "docs.docker.com",
            "https://docs.docker.com/reference/compose-file/",
            "Compose Specification",
        ),
        (
            "product",
            "Terraform provider configuration",
            "developer.hashicorp.com",
            "https://developer.hashicorp.com/terraform/language/providers/configuration",
            "required_providers",
        ),
        (
            "freshness",
            "current Python releases downloads",
            "www.python.org",
            "https://www.python.org/downloads/",
            "Python Releases",
        ),
        (
            "freshness",
            "current Node.js release schedule",
            "github.com",
            "https://github.com/nodejs/release#release-schedule",
            "Release schedule",
        ),
        (
            "freshness",
            "latest Kubernetes releases",
            "kubernetes.io",
            "https://kubernetes.io/releases/",
            "Release History",
        ),
        (
            "freshness",
            "latest GitHub changelog",
            "github.blog",
            "https://github.blog/changelog/",
            "Changelog",
        ),
        (
            "freshness",
            "Chrome releases stable channel",
            "chromereleases.googleblog.com",
            "https://chromereleases.googleblog.com/",
            "Stable Channel",
        ),
        ("freshness", "latest OpenSSL news", "www.openssl.org", "https://www.openssl.org/news/", "News"),
    ]
    cases = []
    for index, (family, query, domain, url, identifier) in enumerate(entries, 1):
        case_id = f"search.live.{family}.{index:02d}"
        cases.append(
            {
                "id": case_id,
                "input": {
                    "case_id": case_id,
                    "lane": "search",
                    "query": query,
                    "max_results": 10,
                    "include_domains": [domain],
                },
                "expected": {
                    "lane": "search",
                    "relevant_urls": [url],
                    "relevant_domains": [domain],
                    "required_identifiers": [identifier],
                },
                "metadata": {
                    "description": f"Manual live {family} search case.",
                    "split": "dev" if index <= 15 else "test",
                    "family": family,
                    "product_area": "live-search",
                    "critical": False,
                    "live": True,
                    "tags": ["manual", "live", family],
                    "reference_checked_at": "2026-07-14",
                    "reference_expires_at": "2026-10-12",
                    "rights": {
                        "redistribution": "unknown",
                        "source": "Manual reference URL; content is never redistributed.",
                    },
                },
            }
        )
    _write_suite(
        BENCH / "cases" / "live-search-v2.yaml",
        "docpull-live-search",
        "Thirty manually dispatched live search queries across five source families.",
        cases,
        None,
    )


def _migrate_v1_suites() -> None:
    for path in sorted((BENCH / "cases").glob("*-v1.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 2
        for case in payload["cases"]:
            lane = case["input"]["lane"]
            case["expected"]["lane"] = lane
            if lane == "extract":
                case["input"].pop("max_pages", None)
                case["input"].pop("max_depth", None)
            if lane == "crawl":
                case["expected"].setdefault("maximum_duplicate_rate", 1.0)
            metadata = case["metadata"]
            metadata.setdefault("family", "legacy-controlled" if not metadata.get("live") else "live")
            metadata.setdefault("product_area", "legacy-v1-migrated")
            metadata.setdefault("critical", not metadata.get("live", False))
            if metadata.get("live"):
                metadata.setdefault("reference_checked_at", "2026-07-14")
                metadata.setdefault("reference_expires_at", "2026-10-12")
        _write(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def _write_legacy_replays() -> None:
    root = BENCH / "replays" / "fixture"
    common = {
        "schema_version": 2,
        "system": "fixture",
        "status": "completed",
        "cost_usd": 0.0,
        "cost_kind": "actual",
        "cost_basis": "Repository replay; no request.",
        "adapter_version": "2",
        "artifacts": {},
    }
    records = [
        {
            "url": "http://127.0.0.1:8765/index.html",
            "title": "Context Fixture Home",
            "content": "# Context Fixture\n\nalpha-731 deterministic evidence from the stable root page. This repository-authored article is deliberately long enough for controlled extraction scoring and redistribution.",
            "metadata": {},
        },
        {
            "url": "http://127.0.0.1:8765/guide.html",
            "title": "Context Fixture Guide",
            "content": "# Fixture Guide\n\nbeta-204 bounded graph evidence from the guide page with a unique explanation of traversal limits and path precision.",
            "metadata": {},
        },
        {
            "url": "http://127.0.0.1:8765/changelog.html",
            "title": "Context Fixture Changelog",
            "content": "# Fixture Changelog\n\ngamma-918 independent crawl evidence from the changelog with a distinct record for duplicate-rate scoring.",
            "metadata": {},
        },
    ]
    extract = {
        **common,
        "case_id": "controlled.extract.article",
        "payload": {
            "kind": "content",
            "records": [records[0]],
            "selected_urls": [records[0]["url"]],
        },
        "elapsed_seconds": 0.001,
        "request_count": 0,
    }
    crawl = {
        **common,
        "case_id": "controlled.crawl.graph",
        "payload": {
            "kind": "content",
            "records": records,
            "selected_urls": [record["url"] for record in records],
        },
        "elapsed_seconds": 0.003,
        "request_count": 0,
    }
    _write(root / "controlled.extract.article.json", json.dumps(extract, indent=2) + "\n")
    _write(root / "controlled.crawl.graph.json", json.dumps(crawl, indent=2) + "\n")


def main() -> None:
    generate_assets()
    generate_suites()
    print("generated deterministic fixture corpus and schema-v2 suites")


if __name__ == "__main__":
    main()

# Functionality Matrix

| Feature | Documented? | Implemented? | Tested? | Current status | Evidence / next step |
| --- | ---: | ---: | ---: | --- | --- |
| Package version 4.3.0 | Yes | Yes | Smoke | Working | `pyproject.toml`; `.venv/bin/python -m docpull --version` |
| CLI `--help` / `--version` | Yes | Yes | Smoke | Working | Final release gate should rerun direct CLI smoke |
| Profiles `rag/mirror/quick/llm/okf` | Yes | Yes | Yes | Working | Profile tests and CLI parser tests |
| `--single` in-memory fast path | Yes | Yes | Yes | Working | Fetcher and scraper API e2e tests |
| Scraper-facing API | Yes | Yes | Yes | Working | `docpull.scraper`, README examples, local e2e test |
| Markdown output | Yes | Yes | Yes | Working | Output e2e tests |
| JSON output | Yes | Yes | Yes | Working | Output e2e tests plus manifest assertion |
| NDJSON / stream output | Yes | Yes | Yes | Working | Output e2e tests plus `sources.md` support |
| SQLite output | Yes | Yes | Yes | Working | SQLite schema tests |
| SQLite FTS retrieval | Yes | Yes | Yes | Working | `documents_fts`, `search_sqlite_documents()` tests |
| OKF output | Yes | Yes | Yes | Working | OKF e2e conformance test |
| Corpus manifest | Yes | Yes | Yes | Working | Manifest assertions and `docs/corpus-manifest.md` |
| Stable document/chunk IDs | Yes | Yes | Yes | Working | `tests/test_document_record.py` |
| Cache/incremental | Yes | Yes | Yes | Working | Existing cache tests; still worth full release gate |
| Resume/frontier | Partial | Yes | Yes | Working but needs docs | Existing frontier/cache tests; update product docs if promoted |
| SSRF controls | Yes | Yes | Yes | Working | Security hardening tests |
| DNS pinning | Yes | Yes | Yes | Working without proxy | Proxy mode remains documented caveat |
| robots.txt mandatory | Yes | Yes | Yes | Working | Robots tests |
| Sitemap discovery | Yes | Yes | Yes | Working | Discovery tests |
| Link crawling | Yes | Yes | Yes | Working | Discovery tests |
| Next.js extraction | Yes | Yes | Yes | Working | Special-case tests |
| Mintlify extraction | Yes | Yes | Yes | Working | Special-case tests |
| Docusaurus extraction/tagging | Yes | Yes | Yes | Working | Static article fixture |
| Sphinx extraction/tagging | Yes | Yes | Yes | Working | Static body fixture |
| MkDocs/Material extraction/tagging | Yes | Yes | Yes | Working | Static content fixture |
| VitePress extraction/tagging | Yes | Yes | Yes | Working | Static content fixture |
| Starlight extraction/tagging | Yes | Yes | Yes | Working | Static content fixture |
| GitBook extraction/tagging | Yes | Yes | Yes | Working | Static content fixture |
| ReadMe.io extraction/tagging | Yes | Yes | Yes | Working | Static content fixture |
| Redoc/Scalar static extraction/tagging | Yes | Yes | Yes | Working | Static API reference fixture |
| OpenAPI extraction | Yes | Yes | Yes | Working | Special-case tests |
| Raw Markdown/text extraction | Yes | Yes | Yes | Working | Special-case tests |
| JS-only detection | Yes | Yes | Yes | Working | Strict/skip tests |
| Rich metadata | Yes | Yes | Yes | Working | Metadata and output tests |
| Python MCP | Yes | Yes | Yes | Working | MCP tests; final release gate should include `docpull mcp --help` |
| Claude plugin docs | Yes | Yes | Yes | Working | Cache path/version regression in `tests/test_ci_policy.py` |
| Root TypeScript MCP | Partial | In-tree | Some TS tests | Strategy risk | Keep marked internal unless deliberately promoted |
| Optional JS rendering | Boundary only | No | No | Out of default scope | Add only behind explicit extra and policy |
| Authenticated/internal docs product mode | Partial primitives | Partial | Partial | Strategic, not promoted | Needs allowlist/redaction/audit design |

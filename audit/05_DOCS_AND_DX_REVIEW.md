# Docs and DX Review

## Accurate Claims

- README positions docpull as a browser-free scraper for static and
  server-rendered pages, with documentation ingestion as the sharpest workflow.
- `docs/scraping-boundary.md` clearly states non-goals: no default JS
  execution, no bot-defense evasion, no proxy-rotation product, no hosted
  scraping API.
- README quickstart documents page graph scraping, `--single`, LLM NDJSON,
  OKF, and mirror/cache workflows.
- README framework table now matches implementation for Next.js, Mintlify,
  OpenAPI, Docusaurus, Sphinx, MkDocs/Material, VitePress, Starlight, GitBook,
  ReadMe.io, and Redoc/Scalar-style static pages.
- README documents scraper-facing Python API (`scrape_one`, `Scraper`) and core
  Fetcher API.
- README and `docs/corpus-manifest.md` document stable IDs, content hashes,
  output paths, and manifest purpose.
- SQLite output now has documented FTS retrieval through
  `search_sqlite_documents()`.
- Security claims are backed by code/tests: SSRF controls, DNS pinning when no
  proxy delegates DNS, XXE-safe sitemap parsing, CRLF/header guards, redirect
  auth stripping, and path traversal checks.

## Remaining Docs / DX Gaps

- Plugin README cache path and version prerequisite currently match
  `src/docpull/mcp/sources.py`, with a regression check in `tests/test_ci_policy.py`.
- Root TypeScript MCP must remain clearly internal/separate unless it becomes a
  deliberate public product.
- Authenticated/internal docs examples are still thin relative to the security
  risk of scoped secrets and private content.
- Optional provider workflows are broad; pack recipes are documented, but a
  smaller "which workflow should I use?" page would reduce agent/user mistakes.
- Console-script smoke should be part of release docs/checklist because stale
  venv shebangs can break `.venv/bin/*` even when `python -m` works.

## AI-Agent DX

- Strong: `--single`, streamed NDJSON, manifests, source indexes, pack scoring,
  MCP fetch/read/grep tools, and scraper-facing API names.
- Improved: SQLite FTS gives local retrieval a path beyond regex-only markdown
  scans.
- Still needed: one unified retrieval story across Markdown cache, NDJSON packs,
  SQLite FTS, and MCP tools.

## Packaging

- `pyproject.toml` declares Python 3.10-3.14 and typed package via `py.typed`.
- Editable install is verified locally against this checkout.
- Final release gate should exercise `.venv/bin/ruff`, `.venv/bin/mypy`,
  `.venv/bin/pytest`, and `python -m docpull` smoke commands.

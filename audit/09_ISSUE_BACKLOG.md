# Issue Backlog

## P0

### Final release gate for current unreleased changes

- Labels: `tests`, `release`, `dx`
- Severity: high
- Evidence: Current worktree adds OKF output, scraper API, SQLite FTS,
  framework extractors, docs, and audit updates.
- Fix: before release, run `.venv/bin/ruff check .`, `.venv/bin/mypy
  src/docpull`, `.venv/bin/pytest -q`, `.venv/bin/python -m docpull --help`,
  `.venv/bin/python -m docpull --version`, and `.venv/bin/python -m docpull
  --doctor`.

## P1

### Add framework fixture extractors

- Labels: `feature`, `tests`, `scraping`
- Severity: medium
- Completed now: Docusaurus, Sphinx, MkDocs/Material, VitePress, Starlight,
  GitBook, ReadMe.io, and Redoc/Scalar-style static extraction fixtures.
- Remaining target: curated live-regression captures and deeper data-feed
  extractors only where static extraction underperforms.

### Add CLI no-network smoke tests for installed entry points

- Labels: `tests`, `dx`
- Severity: high
- Evidence: A stale venv shebang previously broke direct `pytest`/`mypy`
  scripts even when `python -m` worked.
- Fix: test installed entry points in CI or add a local release checklist that
  exercises console scripts after editable install.

### Improve SQLite search surface

- Labels: `feature`, `retrieval`, `mcp`
- Severity: medium
- Current state: SQLite output creates/backfills FTS5 and exposes
  `search_sqlite_documents()`.
- Next step: add CLI or MCP search over `documents.db` when a source has SQLite
  output, with markdown fallback for normal docs caches.

### Strengthen corpus manifest validation

- Labels: `feature`, `architecture`, `tests`
- Severity: medium
- Current state: manifests record stable IDs, hashes, output paths, counts, run
  identity, and chunk provenance; schema documented in `docs/corpus-manifest.md`.
- Next step: add a JSON Schema or stricter pack validation command.

### Verify and document root TypeScript MCP status

- Labels: `mcp`, `architecture`, `docs`
- Severity: medium
- Evidence: Python MCP is the supported package path; root `mcp/` remains a
  separate/internal surface.
- Fix: keep end-user docs focused on Python MCP unless the TS surface is split
  into its own public product.

## P2

### Optional JS renderer

- Labels: `feature`, `security`, `performance`
- Severity: strategic
- Fix: keep browser-free default; add Playwright/Browserless only behind an
  explicit extra, domain allowlists, request/page budgets, and local JS-only
  fixture tests.

### Authenticated/internal docs mode

- Labels: `feature`, `security`
- Severity: high
- Fix: design allowlists, scoped auth headers/cookies, redaction, audit logs,
  privacy mode, and no cross-origin auth before promoting this as a product
  mode.

### Pack and MCP retrieval improvements

- Labels: `mcp`, `retrieval`, `feature`
- Severity: medium
- Fix: unify markdown grep, NDJSON records, SQLite FTS, and pack source maps
  into a predictable local retrieval layer for agents.

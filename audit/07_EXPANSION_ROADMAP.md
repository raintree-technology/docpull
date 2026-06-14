# Expansion Roadmap

## P0: Release The Current Baseline Cleanly

### Final gate current unreleased changes

- User story: As a user, I can install the current checkout and run the CLI,
  API, scraper facade, and output formats without local path drift.
- Current state: editable install now points at `/Users/mb1/Code/raintree/docpull`;
  tests/mypy pass; ruff import-order issue was fixed.
- Files: package metadata, CLI, scraper API, output sinks, docs.
- Tests: `.venv/bin/ruff check .`, `.venv/bin/mypy src/docpull`,
  `.venv/bin/pytest -q`, CLI smoke.
- Acceptance: all gates pass from the normal `.venv/bin/*` entry points.

### Keep OKF output scoped and stable

- User story: As an agent/wiki user, I can generate an OKF bundle without
  colliding with generated `index.md` files.
- Current state: `--format okf` and `--profile okf` write OKF concept
  frontmatter, safe `_root.md` / `_page.md` files, generated indexes, and
  `corpus.manifest.json`.
- Acceptance: OKF e2e tests pass and README warns users to write OKF to a
  clean directory.

### Keep scraper positioning precise

- User story: As a developer, I understand docpull's scraper scope before
  choosing it over Scrapy, Crawlee, hosted extraction APIs, or trafilatura.
- Current state: `docs/scraping-boundary.md` defines browser-free scope and
  non-goals; `docpull.scraper` exposes thin scraper-native APIs over Fetcher.
- Acceptance: docs and API tests demonstrate first-class scraper usage without
  adding a second crawler engine.

## P1: Deepen The Agent-Context Product

### SQLite and local retrieval

- User story: As an agent/RAG builder, I can search locally generated artifacts
  without loading every file into memory.
- Current state: SQLite output creates/backfills an FTS5 index and exposes
  `search_sqlite_documents()`.
- Next steps: add a CLI/MCP search surface that prefers SQLite FTS when present
  and falls back to markdown grep.

### Corpus manifest schema validation

- User story: As a downstream consumer, I can validate pack/manifests before
  indexing them.
- Current state: manifests include stable IDs, content hashes, output paths,
  chunk provenance, counts, and run identity; schema is documented in
  `docs/corpus-manifest.md`.
- Next steps: add JSON Schema and a `docpull pack validate` command.

### Framework fixture expansion

- User story: As a user, common docs frameworks are handled predictably without
  requiring JavaScript rendering.
- Current state: fixtures cover Next.js, Mintlify, OpenAPI, raw text,
  Docusaurus, Sphinx, MkDocs/Material, VitePress, Astro/Starlight, GitBook,
  ReadMe.io, and static Redoc/Scalar-style API reference pages.
- Next targets: live-regression captures and deeper framework-specific data
  feeds where static extraction underperforms.

### Plugin/MCP cache clarity

- User story: As a Claude Code or MCP user, I can find, refresh, and delete the
  actual cache location.
- Current state: `plugin/README.md` points to `docpull-mcp/docs`, matching
  `src/docpull/mcp/sources.py`, and `tests/test_ci_policy.py` checks the
  README path/version text.

## P2: Strategic Expansion

### Optional JS rendering adapter

- User story: As a user, JS-only docs can be fetched when I explicitly opt in.
- Plan: keep browser-free default; add Playwright/Browserless behind a separate
  extra, domain allowlists, page/request budgets, and local JS-only fixtures.
- Risk: security, cost, flakiness.

### Authenticated/internal docs mode

- User story: As an enterprise user, I can fetch private docs safely.
- Plan: allowlist domains, scoped headers/cookies, redaction, audit log,
  cross-origin auth stripping, and privacy mode.
- Risk: high.

### Semantic search product path

- User story: As an agent, I can ask conceptual questions over cached docs.
- Plan: decide whether Python MCP gains optional embeddings adapters or whether
  the root TypeScript MCP remains a separate/internal experiment.

## P3: Speculative Bets

- Malicious-doc detection/redaction.
- Provenance attestations for generated corpora.
- GitHub Actions scheduled refresh workflow generator.
- IDE integrations beyond Claude/Cursor.

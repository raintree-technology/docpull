# Test Plan

## Current Inventory

- Python source files: 73 checked by mypy.
- Test suite: 522 tests passing in the current checkout.
- Categories present: CLI, config, integration, discovery, link extractors,
  conversion, special cases, pipeline, output formats, OKF, scraper API,
  chunking, cache conditional GET, frontier/resume, MCP tools/server, security
  hardening, CI policy, real-site regressions, benchmark/performance, pack
  tools, provider workflows, SQLite, and NDJSON.

## Current Baseline Results

- `.venv/bin/pytest -q`: passed, 522 tests.
- `.venv/bin/mypy src/docpull`: passed, 73 source files.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/python -m docpull --version`: passed, `docpull 4.2.0`.
- `.venv/bin/python -m docpull --help`, `--doctor`, and `mcp --help`: passed.
- `.venv/bin/pytest`, `.venv/bin/mypy`, and editable `docpull` console setup now
  point at `/Users/mb1/Code/raintree/docpull` instead of the stale secondary
  checkout.

## Required Release Gate

P0:

- `.venv/bin/ruff check .`
- `.venv/bin/mypy src/docpull`
- `.venv/bin/pytest -q`
- `.venv/bin/python -m docpull --version`
- `.venv/bin/python -m docpull --help`
- `.venv/bin/python -m docpull --doctor`
- `.venv/bin/python -m docpull mcp --help`

## Missing or Weak Test Areas

- Plugin docs regression: cache path text matches `default_docs_dir()`.
- Installed console-script smoke in CI after editable install.
- SQLite FTS via CLI/MCP once a user-facing search command is added.
- Manifest JSON Schema validation once schema file is added.
- Framework fixtures now cover MkDocs/Material, VitePress, Astro/Starlight,
  GitBook, ReadMe.io, and static Redoc/Scalar-style pages; live-regression
  captures remain useful.
- Security edge tests still valuable: decompression bombs, symlinked cache
  directories, sitemap streaming caps, proxy + pinned DNS behavior in every
  agent-facing path.
- Optional JS renderer tests should be added only when that extra exists.

## Prioritized Regression Suite

P0:

- CLI import/no-network smoke for version/help/doctor/mcp help.
- Output e2e for markdown/json/ndjson/sqlite/okf.
- Scraper API one-page and site-write tests.
- SQLite FTS creation, legacy backfill, and search helper tests.
- Stable document/chunk ID tests.
- Docusaurus/Sphinx static framework fixtures.
- MkDocs, VitePress, Starlight, GitBook, ReadMe.io, and Redoc/Scalar static
  framework fixtures.

P1:

- `docpull pack validate` once manifest schema exists.
- MCP search integration if SQLite FTS becomes an MCP surface.

P2:

- Live framework regression captures.
- JS-only local fixture for the future optional renderer.
- Authenticated/internal docs source-policy tests.

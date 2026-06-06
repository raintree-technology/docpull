# Issue Backlog

## P0

### Import failure blocks all CLI/API/MCP use

- Labels: `bug`, `tests`, `dx`
- Severity: critical
- Evidence: `src/docpull/models/events.py:131-140`; command failures.
- Repro: `./.venv/bin/docpull --version`.
- Expected: `docpull 4.0.0`.
- Actual: `NameError: name 'FetchEvent' is not defined`.
- Fix: postponed annotations or quoted return type; add smoke tests.

### Ruff/mypy failures in dirty worktree

- Labels: `bug`, `tests`, `dx`
- Severity: high
- Evidence: ruff F821 for `FetchEvent` and `SkipReason`; mypy 3 errors.
- Repro: `ruff check .`; `mypy src/docpull`.
- Fix: imports/annotations/typing cleanup.

### LLM profile docs disagree with behavior

- Labels: `docs`, `bug`
- Severity: medium
- Evidence: `profiles.py:47-57`.
- Fix: align config/docs/comment.

### CLI accepts removed naming aliases

- Labels: `bug`, `dx`, `docs`
- Severity: medium
- Evidence: `cli.py:136-143`, `config.py:145-150`, changelog 3.0.0.
- Fix: remove or explicitly reject aliases.

### Plugin README cache path is wrong

- Labels: `plugin`, `docs`, `dx`
- Severity: medium
- Evidence: `plugin/README.md:63-65`, `mcp/sources.py:114-120`.
- Fix: update docs and add regression check.

## P1

### Verify and document root TypeScript MCP status

- Labels: `mcp`, `architecture`, `docs`
- Severity: medium
- Evidence: README mirror claim unverified; TS/Python MCP duplicate tools.
- Fix: split/private/experimental decision and README update.

### Add CLI no-network smoke tests

- Labels: `tests`, `dx`
- Severity: high
- Evidence: current import failure escaped.
- Fix: test `--version`, `--help`, `--doctor`, `mcp --help`.

### Add output format e2e suite

- Labels: `tests`, `feature`
- Severity: medium
- Evidence: formats claimed; runtime not verified.
- Fix: localhost fixture for markdown/json/ndjson/sqlite.

### Add proxy-mode security tests/docs

- Labels: `security`, `tests`, `docs`
- Severity: medium
- Evidence: proxy disables resolver pinning by design.
- Fix: tests and explicit agent guidance.

### Add SQLite/FTS or indexed grep

- Labels: `performance`, `mcp`, `feature`
- Severity: medium
- Evidence: MCP grep scans files line-by-line.
- Fix: optional FTS index.

## P2

### Add framework fixture extractors

- Labels: `feature`, `tests`
- Severity: medium
- Targets: MkDocs, VitePress, VuePress, Starlight, GitBook, ReadMe.io, Redoc/Scalar.

### Add corpus manifest and stable chunk IDs

- Labels: `feature`, `architecture`
- Severity: medium
- Fix: manifest schema, source maps, deterministic chunk IDs.

### Authenticated/internal docs mode

- Labels: `feature`, `security`
- Severity: high
- Fix: allowlists, scoped secrets, redaction, audit logs.

### Optional JS renderer

- Labels: `feature`, `security`, `performance`
- Severity: strategic
- Fix: Playwright/Browserless adapter behind explicit extra and policy.

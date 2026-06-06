# Expansion Roadmap

## P0: Correctness, Security, Documentation

### Fix current import/type/lint blockers

- User story: As a user, I can run `docpull --help` and `docpull --version` after installing from source.
- Why now: Current dirty worktree is unusable.
- Evidence: `FetchEvent` NameError in `src/docpull/models/events.py:131-140`; ruff/mypy failures.
- Plan: add postponed annotations, import `SkipReason` where used, fix ruff/mypy issues.
- Files: `models/events.py`, `pipeline/steps/convert.py`, `cache/frontier.py`, formatting.
- Tests: CLI smoke, import smoke, full pytest.
- Docs: none.
- Risk: S.
- Estimate: S.
- Acceptance: `ruff check .`, `mypy src/docpull`, `pytest -q`, `docpull --help` pass.

### Align LLM profile JS policy

- User story: As an agent, I know whether LLM mode skips or fails JS-only pages.
- Evidence: README/inline comment vs `strict_js_required=False`.
- Plan: choose behavior; likely keep skip default for compatibility and update comment/README, or set true in v5.
- Files: `profiles.py`, README, website, tests.
- Tests: profile contract test.
- Risk: behavior change if set true.
- Estimate: S.
- Acceptance: docs, comments, and config agree.

### Fix CLI naming aliases

- User story: As a CLI user, deprecated `flat/short` behavior is not accepted silently then rejected later.
- Evidence: `cli.py:136-143`, `config.py:145-150`, changelog 3.0.0.
- Plan: remove `flat/short` from argparse or map with clear error.
- Tests: parser/config test.
- Risk: low.
- Estimate: S.
- Acceptance: `docpull --help` only lists valid names.

### Correct plugin docs

- User story: As a Claude Code user, I can find/delete the actual cache.
- Evidence: plugin README path mismatch.
- Plan: update cache path, version prerequisite, and direct URL wording.
- Tests: docs lint.
- Estimate: S.

## P1: High-Impact Low-Risk

### Stable corpus manifest and chunk IDs

- User story: As a RAG builder, I can diff and cite regenerated docs reliably.
- Code seam: `DocumentRecord`, `RunIdentity`, `NdjsonSaveStep`, `SqliteSaveStep`.
- Plan: add `manifest.json`, stable chunk IDs from URL+heading+chunk index+content hash.
- Tests: deterministic rerun tests.
- Docs: output schema page.
- Estimate: M.

### SQLite FTS search path

- User story: As an agent, I can search cached docs faster than regex scan without Postgres.
- Code seam: `save_sqlite.py`, MCP `grep_docs`.
- Plan: create SQLite FTS5 index; add optional MCP source `search_docs` or faster grep.
- Tests: FTS ranking, migration, path validation.
- Estimate: M.

### Per-project MCP cache

- User story: As a team, each repo has its own docs snapshot.
- Code seam: `default_docs_dir()`, plugin config, commands.
- Plan: support `.docpull/docs` or env/config override in plugin commands.
- Tests: XDG/env/project precedence.
- Estimate: M.

### Framework extractor fixture suite

- User story: As a user, DocPull handles common docs frameworks predictably.
- Targets: MkDocs/Material, VitePress, VuePress, Astro/Starlight, GitBook, ReadMe.io, Redoc/Scalar.
- Code seam: `conversion/special_cases.py`.
- Plan: add local HTML fixtures before live extractors.
- Tests: one fixture per framework.
- Estimate: M.

## P2: Strategic Expansion

### Optional JS rendering adapter

- User story: As a user, JS-only docs can be fetched when I explicitly opt in.
- Plan: keep browser-free default; add Playwright/Browserless adapter behind extra and strict domain/budget controls.
- Risk: security, cost, flakiness.
- Estimate: L.
- Acceptance: JS-only local fixture succeeds only with adapter enabled.

### Authenticated/internal docs mode

- User story: As an enterprise user, I can fetch private docs safely.
- Plan: allowlist domains, scoped headers/cookies, redaction, audit log, no cross-origin auth, privacy mode.
- Files: config, HTTP, docs, MCP tools.
- Risk: high.
- Estimate: L/XL.

### Semantic search product path

- User story: As an agent, I can ask conceptual questions over cached docs.
- Plan: decide whether root `mcp/` becomes official or Python MCP gains optional embeddings adapters.
- Dependencies: OpenAI/local embeddings/vector DB.
- Estimate: L.

## P3: Speculative Bets

- Malicious-doc detection/redaction.
- Provenance attestations for generated corpora.
- GitHub Actions scheduled refresh workflow generator.
- IDE integrations beyond Claude/Cursor.


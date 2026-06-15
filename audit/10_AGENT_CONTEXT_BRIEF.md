# Agent Context Brief

DocPull is a Python 3.10+ package/CLI at version 4.3.0. It is a browser-free
web scraper for static/server-rendered docs and pages, with agent-context output
formats: Markdown, JSON, NDJSON, SQLite, OKF, manifests, source indexes, and
MCP tools. The repo also contains optional Parallel provider workflows, a
Claude Code plugin, a website, and a separate/internal Bun/TypeScript MCP lab.

Current workspace is dirty but functional. The dirty work adds unreleased OKF
output, a scraper-facing API, SQLite FTS search, Docusaurus/Sphinx fixture
extractors, manifest/schema docs, and audit cleanup. Do not treat these changes
as released until the final gate passes from normal `.venv/bin/*` entry points.

Core flow: CLI / scraper API / Python API / MCP -> `DocpullConfig` -> `Fetcher`
-> `UrlValidator` / `RobotsChecker` / `AsyncHttpClient` -> sitemap/link
discovery -> `FetchPipeline` -> validate/fetch/convert/metadata/dedup/chunk/save.

Security model: HTTPS-only default, private/internal/localhost/CGNAT and
IPv4-mapped IPv6 blocks, DNS root-dot stripping, connect-time DNS pinning when
not using a proxy, redirect revalidation, cross-origin auth stripping, robots
handling, defusedxml for sitemap XML, YAML frontmatter sanitization, MCP path
guards, and regex timeouts.

Current highest-priority checks:

- Keep `.venv/bin/ruff check .`, `.venv/bin/mypy src/docpull`, and
  `.venv/bin/pytest -q` green.
- Smoke `.venv/bin/python -m docpull --version`, `--help`, `--doctor`, and
  `mcp --help` before release.
- Keep plugin README cache path/version regression green.
- Keep scraper positioning narrow: local, auditable, browser-free
  web-to-context extraction, not general browser automation.
- Add new framework support fixture-first.

Useful commands:

```bash
.venv/bin/python -m docpull --version
.venv/bin/python -m docpull --help
.venv/bin/python -m docpull --doctor
.venv/bin/python -m docpull mcp --help
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src/docpull
.venv/bin/bandit -q -c pyproject.toml -r src
```

Do not run uncontrolled crawls. Use localhost fixtures, `--single`,
`--max-pages`, and `--dry-run` / `--preview-urls` when exploring behavior.

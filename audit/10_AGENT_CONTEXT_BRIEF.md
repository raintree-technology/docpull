# Agent Context Brief

DocPull is a Python 3.10+ package/CLI at version 4.0.0. It fetches static docs over async HTTP, validates URLs, respects robots.txt, converts to Markdown/JSON/NDJSON/SQLite-style records, and ships a Python stdio MCP server. The repo also contains a separate Bun/TypeScript `mcp/` semantic-search MCP with Postgres/pgvector and a Claude Code plugin.

Current workspace is dirty and broken. Do not assume it represents clean public 4.0.0. `docpull --version`, `--help`, `--doctor`, `pytest`, and coverage all fail because `src/docpull/models/events.py` references `FetchEvent` in a return annotation before the class exists. Fix this before any feature work.

Core flow: CLI -> `DocpullConfig` -> `Fetcher` -> `UrlValidator`/`RobotsChecker`/`AsyncHttpClient` -> sitemap/link discovery -> `FetchPipeline` -> validate/fetch/convert/metadata/dedup/chunk/save.

Security model: HTTPS-only, private/internal/localhost/CGNAT/IPv4-mapped IPv6 blocks, DNS root-dot stripping, connect-time DNS pinning via aiohttp resolver, redirect revalidation, cross-origin auth stripping, robots pinned fetch, defusedxml for sitemap XML, YAML frontmatter sanitization, MCP path and regex guards.

Highest-priority gaps:
- Fix import/lint/type/test failures.
- Align LLM profile JS-only behavior with docs.
- Remove CLI `flat`/`short` naming aliases or update config/docs.
- Fix plugin cache path docs: implementation uses `~/.local/share/docpull-mcp/docs`.
- Decide/document root TypeScript MCP status; public mirror claim is unverified.

Useful commands after fixing import blocker:

```bash
./.venv/bin/docpull --version
./.venv/bin/docpull --help
./.venv/bin/docpull --doctor
./.venv/bin/pytest -q
./.venv/bin/ruff check .
./.venv/bin/mypy src/docpull
./.venv/bin/bandit -q -c pyproject.toml -r src
```

Do not run uncontrolled crawls. Use localhost fixtures and `--single` / `--max-pages`.

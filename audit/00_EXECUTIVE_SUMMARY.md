# Executive Summary

## What DocPull Is

DocPull is a Python CLI/library that fetches static documentation pages over async HTTP, validates URLs against SSRF policy, respects robots.txt, converts content to Markdown/JSON/NDJSON/SQLite-oriented records, and exposes a package-shipped Python stdio MCP server for agent use. The repo also contains a separate Bun/TypeScript `mcp/` server with PostgreSQL/pgvector semantic search and a Claude Code plugin bundle.

Audit target/version evidence:
- `pyproject.toml:6-7` declares package `docpull` version `4.0.0`.
- `python -m pip show docpull` in `.venv` reports editable package `Version: 4.0.0`.
- Git `HEAD` is `a3a288e chore(release): 4.0.0 (#48)`.
- Current worktree is dirty and currently broken at import time; this is not a clean 4.0.0 runtime.

## What Works

Verified by static evidence and tests present in the repo:
- Strong URL validation design: HTTPS-only defaults, localhost/internal suffix blocks, private/link-local/reserved/multicast/CGNAT IP blocks, IPv4-mapped IPv6 handling, and single-resolution `resolve_allowed_addresses()` in `src/docpull/security/url_validator.py:48-197`.
- Connect-time DNS pinning in aiohttp when no proxy is configured via `_ValidatedResolver` in `src/docpull/http/client.py:32-76` and `AsyncHttpClient.__aenter__` in `src/docpull/http/client.py:269-285`.
- Redirect revalidation and auth stripping across origins in `src/docpull/http/client.py:203-267`.
- robots.txt fail-closed behavior, redirect handling, pinned HTTPS connection, and 512 KB cap in `src/docpull/security/robots.py:97-203`.
- Sitemap XML parsing uses `defusedxml` with size/depth limits in `src/docpull/discovery/sitemap.py:9-43` and `149-186`.
- Python MCP tool surface and schemas are defined for 8 tools with annotations and structured output in `src/docpull/mcp/server.py:225-485`.
- MCP path traversal and ReDoS mitigations exist in `src/docpull/mcp/tools.py:462-628` and `631-706`.

## What Is Risky

Top confirmed risks/gaps:
1. **Critical local worktree breakage**: `docpull --version`, `docpull --help`, `docpull --doctor`, tests, and coverage all fail to import because `FetchEvent.progress()` annotates `-> FetchEvent` without postponed annotations in `src/docpull/models/events.py:131-140`.
2. **Docs/runtime mismatch**: README says LLM profile is agent/LLM-ready and the profile comment says fail-loud on JS-only pages, but `ProfileName.LLM` sets `strict_js_required=False` in `src/docpull/models/profiles.py:47-64`.
3. **CLI/config mismatch**: CLI accepts `--naming-strategy flat|short` in `src/docpull/cli.py:136-143`, while `OutputConfig.naming_strategy` only accepts `full|hierarchical` in `src/docpull/models/config.py:145-150`.
4. **Plugin cache path mismatch**: plugin README claims `$XDG_DATA_HOME/docpull/docs` / `~/.local/share/docpull/docs`, but Python MCP defaults to `$XDG_DATA_HOME/docpull-mcp/docs` / `~/.local/share/docpull-mcp/docs` in `src/docpull/mcp/sources.py:114-120`.
5. **README mirror claim unverified**: README claims root `mcp/` is mirrored to `raintree-technology/docpull-mcp` in `README.md:211-219`; browser/search evidence did not verify a public mirror.
6. **Current audit could not run functional crawl tests** because import failure blocks runtime validation.
7. **pip install and pip-audit were blocked by DNS/network restrictions**, so dependency vulnerability verification is incomplete.
8. **Proxy mode weakens DNS-pinning guarantees by design**; `--require-pinned-dns` exists, but default proxy behavior still relies on users/agents understanding the warning.
9. **Root TypeScript MCP is duplicated product surface** with different tools, versioning, cache metadata, and deployment assumptions than Python MCP.
10. **Performance claims are only partially verified**: benchmark test code exists, but current worktree cannot collect tests.

## Top 10 Improvements

1. Fix the current import blocker and make `ruff`, `mypy`, `pytest`, and CLI smoke tests green before any expansion.
2. Align LLM profile docs/comment/behavior: either set `strict_js_required=True` or remove fail-loud wording.
3. Remove deprecated `flat`/`short` from CLI choices or map them before Pydantic validation with explicit deprecation messaging.
4. Fix plugin README cache path and version prerequisite (`2.5.0 or newer` should be `4.0.0 or newer` if current plugin targets v4).
5. Decide root `mcp/` strategy: split to public repo, mark experimental/private, or fold roadmap into Python MCP.
6. Add a clean-release CI gate that imports `docpull`, runs `docpull --version`, `--help`, `--doctor`, and `docpull mcp --help`.
7. Add end-to-end localhost fixtures for every advertised output format and profile.
8. Add security regression tests for proxy warning/`--require-pinned-dns`, decompression bombs, symlink cache/output attacks, and source registry poisoning.
9. Add docs architecture/security model pages so users understand browser-free constraints and trust boundaries.
10. Prioritize agent-native output work: stable chunk IDs, manifest schema, citation/source maps, and optional SQLite FTS.

## 30/60/90 Day Recommendation

30 days: stabilize correctness/security/docs. Fix current runtime breakage, resolve claim gaps, publish a patch, and add CLI/MCP smoke tests that would have caught the import failure.

60 days: productize agent workflows. Harden plugin docs, add per-project caches, `/docs-skill`, stable chunk IDs, manifest schema, and a verified cookbook against small fixture docs sites.

90 days: strategic expansion. Add optional JS rendering adapter, authenticated/internal docs model, semantic search path, SQLite FTS, and framework extractors for MkDocs, VitePress, Starlight, GitBook, ReadMe.io, and Redoc/Scalar variants.


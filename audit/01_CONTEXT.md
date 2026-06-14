# Context

## Repo Map

- `src/docpull/`: shipped Python package.
- `tests/`: Python unit, integration, MCP, security, and benchmark tests.
- `docs/`: changelog and YAML example configs.
- `plugin/`: Claude Code plugin metadata, commands, and `docpull-research` skill.
- `.claude-plugin/plugin/`: untracked generated plugin bundle in current worktree.
- `mcp/`: separate Bun/TypeScript MCP server with PostgreSQL/pgvector semantic search.
- `web/`: Next.js product site.
- `.github/`: CI, security, publish, benchmark, metrics, templates, security policy.

## Python Package Architecture

- CLI: `src/docpull/cli.py` builds argparse options and maps them into `DocpullConfig`.
- Config/models: `src/docpull/models/config.py`, `profiles.py`, `events.py`, `run.py`, `document.py`.
- Core orchestrator: `src/docpull/core/fetcher.py`.
- HTTP: `src/docpull/http/client.py`, `rate_limiter.py`.
- Security: `src/docpull/security/url_validator.py`, `robots.py`.
- Discovery: `src/docpull/discovery/sitemap.py`, `crawler.py`, `filters.py`, `link_extractors/`.
- Conversion: `src/docpull/conversion/extractor.py`, `markdown.py`, `special_cases.py`, `trafilatura_extractor.py`, `chunking.py`.
- Pipeline: `src/docpull/pipeline/base.py` plus `steps/validate.py`, `fetch.py`, `convert.py`, `metadata.py`, `dedup.py`, `chunk.py`, `save.py`, `save_json.py`, `save_ndjson.py`, `save_sqlite.py`, `save_okf.py`.
- Cache/resume: `src/docpull/cache/manager.py`, `streaming_dedup.py`, dirty-worktree `frontier.py`.
- Python MCP: `src/docpull/mcp/server.py`, `tools.py`, `sources.py`.
- Scraper facade: `src/docpull/scraper.py`.

## Runtime Data Flow

1. CLI parses args in `src/docpull/cli.py:48-369`.
2. CLI maps args to `DocpullConfig` in `src/docpull/cli.py:372-528`.
3. `Fetcher` applies profile defaults in `src/docpull/core/fetcher.py:167-176`.
4. `Fetcher.__aenter__` builds validator, robots checker, HTTP client, discoverers, cache, and pipeline.
5. Discovery uses sitemaps plus link crawling: `SitemapDiscoverer` and `LinkCrawler`.
6. Each URL enters `FetchPipeline.execute_result()` in `src/docpull/pipeline/base.py:181-222`.
7. Pipeline validates URL/robots/cache, fetches bytes, converts/special-cases, extracts metadata, deduplicates/chunks, and saves.
8. Output writers persist Markdown, JSON, NDJSON, SQLite, or OKF records.
9. `Fetcher.run()` emits `FetchEvent` records for CLI/MCP/progress consumers.

## Trust Boundaries

- User/agent-controlled URL enters CLI, Python API, or MCP `fetch_url`/`add_source`.
- DNS resolver is adversarial for SSRF/DNS-rebinding assumptions.
- Remote HTML/XML/JSON/YAML-ish metadata is untrusted.
- robots.txt and sitemap XML are untrusted network input.
- Output/cache directories are local filesystem trust boundaries; symlinks and traversal matter.
- MCP exposes local cached Markdown to AI hosts; library/path validation matters.
- Auth headers/cookies are secrets and must not cross origins or leak in logs/errors.
- Root `mcp/` adds database and OpenAI API boundaries.

## Extension Seams

- Add URL policy rules in `UrlValidator`.
- Add discovery sources via `Discoverer` protocol and `CompositeDiscoverer`.
- Add framework extraction via `SpecialCaseExtractor` chain in `conversion/special_cases.py`.
- Add output formats as pipeline save steps.
- Add MCP tools in `src/docpull/mcp/server.py` and implementations in `tools.py`.
- Add Claude Code UX in `plugin/commands` and `plugin/skills`.
- Add semantic/FTS storage in SQLite or root `mcp/` DB layer.
- Add scraper-facing convenience APIs in `src/docpull/scraper.py` without duplicating the Fetcher engine.

## Duplicated/Dead Systems

- Python MCP and root TypeScript MCP are separate products with different capabilities and operational models.
- Root `mcp/package.json:2-3` says `docpull-mcp` version `0.3.0`, while `mcp/src/server.ts:396` initializes server version `0.2.0`.
- Root `mcp/` should stay clearly documented as a separate/internal surface unless it is deliberately split or promoted.
- Current dirty worktree adds unreleased OKF output, scraper API, SQLite FTS, framework extractors, and docs/audit updates; local test/type/lint gates should be rerun before release.

## Dependency Graph

```text
CLI/Python API/MCP
  -> DocpullConfig + profiles
  -> Fetcher
    -> UrlValidator + RobotsChecker
    -> AsyncHttpClient + rate limiters
    -> CompositeDiscoverer
      -> SitemapDiscoverer
      -> LinkCrawler + link extractors
    -> FetchPipeline
      -> ValidateStep
      -> FetchStep
      -> ConvertStep
        -> special-case extractors
        -> MainContentExtractor
        -> HtmlToMarkdown + FrontmatterBuilder
      -> MetadataStep
      -> DedupStep
      -> ChunkStep
      -> SaveStep / JsonSaveStep / NdjsonSaveStep / SqliteSaveStep / OkfSaveStep
    -> CacheManager / StreamingDeduplicator
```

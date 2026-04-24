# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3.0] - 2026-04-24

Sharpened positioning around the agent / RAG use case, plus real bug fixes
surfaced by validation against Next.js, Supabase, Anthropic, FastAPI, Tailwind,
and Drizzle documentation sites.

### Added
- **Framework-specific fast extractors**: Next.js `__NEXT_DATA__`, Mintlify,
  OpenAPI / Swagger JSON rendered directly to Markdown, plus source-type
  tagging for Docusaurus and Sphinx. Runs before the generic extractor.
- **Next.js App Router detection** via `self.__next_f.push`, router state tree,
  and `/_next/static/` path markers — no longer relies on `__NEXT_DATA__`,
  which is absent on modern App Router pages.
- **SPA detection (pre- and post-conversion)**: pages that produce only
  `Loading...` shells are skipped with a clear reason. `--strict-js-required`
  turns this into a hard error for agents that want to route elsewhere.
- **Trafilatura extractor** as an optional alternative content extractor
  (`pip install docpull[trafilatura]`, then `--extractor trafilatura`).
- **Token-aware Markdown chunking**: `--max-tokens-per-file N` splits pages
  on heading then paragraph boundaries. Exact counts with `tiktoken`,
  character-estimate fallback otherwise.
- **NDJSON output format** (`--format ndjson`) for streaming one record per
  page or per chunk. `--stream` writes to stdout for live pipeline consumption.
- **`llm` profile**: bundles NDJSON + 4k-token chunks + rich metadata + dedup.
- **`--single` / `fetch_one(url)`**: fast single-page path with no discovery,
  designed for AI-agent tool loops.
- **Python MCP server** (`docpull mcp`): exposes `fetch_url`, `ensure_docs`,
  `list_sources`, `list_indexed`, and `grep_docs` tools over stdio. Install
  via `pip install docpull[mcp]`.

### Fixed
- **robots.txt redirect handling**: Cloudflare/HTTP-2 responses send
  lowercase header names, but the `Location` lookup was case-sensitive,
  causing 301/308 redirects to be treated as errors. This blocked
  `docs.anthropic.com` and any other site whose robots.txt was redirected.
- **html2text link escape artifacts**: cleaned up mangled links of the form
  `[text](prefix/<https:/real.url>)` in the post-processing pass; handles
  both text and image-only (empty-text) links.

### Removed
- Dead dependencies: `requests` (replaced by `aiohttp` in v2.0) and
  `gitpython` (never used in v2+).

### Changed
- `ContentFilterConfig` gains `extractor`, `enable_special_cases`, and
  `strict_js_required` fields. `OutputConfig` gains `max_tokens_per_file`,
  `tokenizer`, `emit_chunks`, and `ndjson_filename`.

## [2.0.0] - 2025-11-29

### Breaking Changes
- Complete architecture rewrite with new Python API
- Moved to `src/` layout (PEP 517/518 compliant)
- Old `GenericAsyncFetcher` replaced by `Fetcher` class with async context manager
- Configuration now uses Pydantic models (`DocpullConfig`)

### Added
- **Streaming event API**: Async iterator interface for real-time progress tracking
- **CacheManager**: Persistent caching with O(1) lookups, batched writes, TTL eviction
- **StreamingDeduplicator**: Real-time duplicate detection during fetch
- **Profiles**: Built-in `rag`, `mirror`, `quick` profiles with sensible defaults
- **CLI cache options**: `--cache`, `--cache-dir`, `--cache-ttl`, `--no-skip-unchanged`
- **Pipeline architecture**: Modular steps (Validate, Fetch, Convert, Dedup, Save)

### Changed
- Cache uses sets internally for O(1) URL membership checks
- Consistent SHA-256 hashing across cache and dedup (accepts str or bytes)
- ETag and Last-Modified headers now extracted and cached

### Removed
- Old fetcher classes (`GenericAsyncFetcher`, `AsyncDocFetcher`, etc.)
- `DedupTracker` replaced by `StreamingDeduplicator`
- Legacy config fields (`incremental`, `update_only_changed`)

## [1.5.0] - 2025-11-28

### Added
- **Proxy support**: HTTP, HTTPS, SOCKS5 via `--proxy` or `DOCPULL_PROXY` env var
- **Retry with exponential backoff**: `--max-retries`, `--retry-base-delay` for transient failures
- **Better encoding detection**: Intelligent charset detection for international docs
- **URL normalization**: Reduces duplicate fetches by 10-20%
- **Content hash change detection**: SHA-256 hashing for efficient incremental updates
- **Custom User-Agent**: `--user-agent` flag

### Changed
- robots.txt compliance is now mandatory (cannot be disabled)
- Automatically respects Crawl-delay directives

## [1.4.0] - 2025-11-28

### Breaking Changes
- Removed profile system entirely - use URLs directly
- `--source` flag removed; use positional URL arguments
- Python API: `url` parameter instead of `url_or_profile`

## [1.3.0] - 2025-11-20

### Added
- **Rich metadata extraction**: `--rich-metadata` extracts Open Graph, JSON-LD, microdata
- Enhanced frontmatter with author, description, keywords, images, publish dates

### Changed
- Removed 7 built-in profiles; generic fetcher works for all sites

## [1.2.0] - 2025-11-16

### Added
15 major features for optimization and workflow automation:

**Optimization**
- `--language` / `--exclude-languages`: Filter by language
- `--deduplicate` / `--keep-variant`: Remove duplicate files
- `--max-file-size` / `--max-total-size`: Size limits
- `--exclude-sections`: Remove verbose sections

**Output**
- `--format`: markdown, toon, json, sqlite
- `--naming-strategy`: full, short, flat, hierarchical
- `--create-index`: Generate INDEX.md

**Workflow**
- `--sources-file`: Multi-source YAML configuration
- `--incremental` / `--update-only-changed`: Resume and update detection
- `--git-commit` / `--git-message`: Git integration
- `--archive` / `--archive-format`: Create archives
- `--post-process-hook`: Python plugin system

### Changed
- PyYAML and GitPython now required dependencies

## [1.1.0] - 2025-11-14

### Added
- `--doctor` command for installation diagnostics
- TROUBLESHOOTING.md documentation

## [1.0.0] - 2025-11-07

### Added
- Initial release
- Async + parallel fetching
- Security: HTTPS-only, path traversal protection, XXE protection, size limits
- Rate limiting and timeout controls
- YAML frontmatter in output files

---

[2.0.0]: https://github.com/raintree-technology/docpull/releases/tag/v2.0.0
[1.5.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.5.0
[1.4.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.4.0
[1.3.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.3.0
[1.2.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.2.0
[1.1.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.1.0
[1.0.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.0.0

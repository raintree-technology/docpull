# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.4.0] - 2026-04-26

A two-pass cleanup. The first pass closed every claim the code didn't back
(seven scaffolded-but-unwired config fields, the no-op fence-language regex,
the "ETag-based caching" claim that never sent `If-None-Match`, the
robots.txt UA mismatch, the `MdxSourceExtractor` dead branch). The second
pass earned the local-first / agent-native / zero-trust pitch the marketing
points at: cookie-banner stripping, rich frontmatter, `--skill` mode,
streaming discovery, conditional GET, and a measured 10k-page benchmark.

### Added
- **Conditional GET on cached pages**: `FetchStep` now sends `If-None-Match`
  and `If-Modified-Since` from the manifest, and a `304 Not Modified`
  response short-circuits with `SkipReason.CACHE_UNCHANGED`. Previously
  the marketing claimed ETag-based skipping but the headers were never
  actually sent. Re-runs against an unchanged site now transfer near-zero
  bytes.
- **Hierarchical naming**: `output.naming_strategy: hierarchical` (set by
  the Mirror profile) preserves URL paths as nested directories
  (`/api/auth/oauth2` → `api/auth/oauth2.md`), with sanitized segments
  and trailing-slash → `index.md` collapse. Path-traversal segments
  (`..`) are neutralized so URL-driven escapes can't leave the output dir.
- **`--skill NAME`** generates a Claude Code skill directory:
  `docpull URL --skill foo` produces `<output-dir>/foo/SKILL.md` plus
  hierarchically named pages. The manifest's `description` field is
  derived from the first page's OpenGraph or JSON-LD metadata, with
  `--skill-description` available as an explicit override.
- **`--require-pinned-dns`** refuses proxy configurations that delegate
  DNS to the proxy. Before this, running with `--proxy` silently weakened
  the SSRF posture (only a startup warning was logged); the new flag
  makes the trade-off explicit. Default off; intended for agent-driven
  workflows.
- **`--no-streaming-discovery`**: backstop flag for the new producer-
  consumer fetch pipeline (see Changed). Falls back to the legacy
  discover-all-then-fetch behavior in case backpressure regressions
  surface in the wild.
- **`max_file_size`** content-filter is now wired to the HTTP client's
  per-response cap. Previously hardcoded at 50 MiB; users can now lower
  it for OOM-prevention on runaway responses.
- **Rich frontmatter**: every Markdown file now ships with a heading
  outline (top-level `h1`/`h2`, ≤12 entries), an ISO 8601 `crawled_at`
  timestamp, OpenGraph `description`, and a whitelisted slice of
  JSON-LD/microdata fields (`author`, `published_time`, `keywords`, etc.).
  Previously OG/JSON-LD extraction ran but the result was dropped.
- **MCP surface polish**: `ensure_docs` accepts a `profile` argument
  (rag/mirror/quick/llm); `grep_docs` ranks results by per-file match
  density and renders ±1 line of context per hit (configurable via
  `context`); `list_indexed` reports humanized fetch age per source;
  `fetch_url` includes chunk count in its response header.
- **10,000-page benchmark**: `tests/benchmarks/test_10k_pages.py`
  stands up a synthetic localhost site with injected duplicates and
  reports wall time, peak RSS delta, manifest size, p50/p95/p99
  per-page latency, and time-to-first-save. Gated behind
  `DOCPULL_BENCHMARK_10K=1`. README's new `## Performance` section
  documents the headline numbers.

### Fixed
- **Code-fence language normalization**: html2text emits `[code]…[/code]`
  blocks without language tags by default, and the post-conversion regex
  meant to fix this was a self-replace no-op. Pages with Prism
  (`class="language-python"`), highlight.js (`lang-py` / `hljs-language-X`),
  Shiki, or GitHub-style (`highlight-source-rust`) syntax classes now
  produce GFM fenced blocks with the right language tag. `plaintext` /
  `text` / `none` are correctly treated as "no language."
- **Cookie / consent banner leakage**: the FAQ claimed common banners
  were stripped, but no selectors targeted the vendor SDK shapes. Added
  selectors for OneTrust, Osano, Cookiebot, CookieLaw, CookieConsent,
  Iubenda, Termly, and generic `.cookie-*` / `.gdpr-*` / `.consent-*`
  patterns plus `aria-label*="cookie|consent|gdpr"` fallbacks. Pages
  that legitimately discuss cookies in their body are unaffected
  (selectors are structural, not text-based).
- **Streaming dedup hashed full Markdown including frontmatter**:
  meant two URLs serving byte-identical body content never deduped
  because their `source:` and (after this release) `crawled_at:`
  fields differed. Dedup now strips frontmatter before hashing — the
  point of streaming dedup is "same body content," not "same bytes."
- **Robots.txt UA mismatch**: docpull matched robots.txt rules as
  `docpull/2.0` while sending requests as `Mozilla/5.0 ... AppleWebKit ...`.
  Site operators scoping rules at `User-Agent: docpull` got no effect.
  Both surfaces now use the same UA, derived from the HTTP client.
- **Empty `cache-fresh` short-circuit when output is missing**: if a
  user cleared `output/` but kept `cache/`, the new conditional GET
  would have skipped on 304 and left no Markdown on disk. `FetchStep`
  now suppresses conditional headers when the expected output file is
  absent, forcing a fresh re-fetch.

### Changed
- **Default User-Agent**: `docpull/{version} (+https://github.com/raintree-technology/docpull)`,
  replacing the previous Mozilla camouflage. Sites that whitelisted
  Mozilla patterns may need to allow the new UA; `--user-agent` continues
  to override. The pitch is "polite crawler" — disguising as a browser
  contradicted that.
- **Streaming discovery → fetch (default)**: URLs now flow through a
  bounded worker pool as the discoverer yields them, instead of being
  collected to a list before any fetching begins. First `PAGE_SAVED`
  on a 10,000-page synthetic site now fires within ~70 ms of the run
  starting (vs. waiting for full discovery before). The discoverer
  awaits when `url_queue` is full, so backpressure self-regulates.
  `--no-streaming-discovery` falls back to the legacy path.
- **Mirror profile** keeps flat naming by default in 2.x to preserve
  existing users' output paths. Hierarchical is now opt-in via
  `--naming-strategy hierarchical` (CLI) or `output.naming_strategy`
  (YAML). The Mirror profile default flips to hierarchical in 3.0; the
  upgrade will be flagged in the 3.0 release notes.
- **`MdxSourceExtractor`** removed from `DEFAULT_CHAIN` (it always
  returned `None`). The class and `find_mdx_source_url` helper are
  still exported for callers that want to wire `prefer_source` manually.

### Deprecated
The following config fields warn at runtime when set to a non-default
value and will be removed in 3.0. Each was scaffolded but never read by
the pipeline; CLI flags backed by them have been dropped from `--help`.
- `content_filter.language` and `--language` (no language detector ever
  shipped — pursue if a real user asks)
- `content_filter.exclude_languages`
- `content_filter.deduplicate` (post-processing dedup; `streaming_dedup`
  covers the use case)
- `content_filter.exclude_sections`
- `content_filter.max_total_size` (cumulative byte budget across an
  async fetch is racy; per-page `max_file_size` is the right knob)
- `output.create_index` (no `INDEX.md` generator; downstream tools
  don't need one)

### Security
- **Honest UA disclosure**: see Changed. Polite-crawling claim now lines
  up with what site operators see in their logs.
- **`--require-pinned-dns`**: see Added. Closes a previously-undisclosed
  gap where `--proxy` disabled docpull's connector-level DNS pinning.
- **Hierarchical-naming traversal**: URL path segments are sanitized
  (`..` → `index`, special chars → `_`, runs of underscores collapsed)
  before being joined into the output path. The `SaveStep` base-dir
  guard remains the second line of defense.

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

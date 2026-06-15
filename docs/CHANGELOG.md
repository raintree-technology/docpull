# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [4.3.1] - 2026-06-15

### Changed
- Tighten PyPI, GitHub, README, and website metadata around the public-web to
  agent-ready Markdown positioning.
- Add launch copy, comparison guidance, and marketing visibility research for
  developer, Python, MCP, and RAG discovery channels.

## [4.3.0] - 2026-06-14

### Added
- Add first-class Open Knowledge Format output via `--format okf` and
  `--profile okf`, including OKF concept frontmatter, generated directory
  `index.md` files with root `okf_version: "0.1"`, and docpull corpus
  manifests.
- Add the `docpull.scraper` API surface (`Scraper`, `scrape_one`,
  `scrape_site`, and `ScrapeResult`) as thin scraper-native names over the
  existing browser-free Fetcher pipeline.
- Add `docs/scraping-boundary.md` to define docpull as a local, auditable
  static/server-rendered web-to-context scraper rather than a general browser
  automation framework.
- Add SQLite FTS5 indexing for `--format sqlite` output plus
  `search_sqlite_documents()` for local full-text retrieval.
- Add static Docusaurus, Sphinx, MkDocs/Material, VitePress, Starlight,
  GitBook, ReadMe.io, and Redoc/Scalar-style extraction fixtures so common
  docs frameworks are extracted and tagged without JavaScript rendering.

## [4.2.0] - 2026-06-08

### Added
- Add `docpull benchmark quick` for repeatable real-site benchmark reports that
  compare core docpull crawls, cached reruns, and optional live Parallel Search
  / Search + Extract context-pack cases behind a local cost guard.
- Add `docpull benchmark article` to turn benchmark JSON reports into a
  publishable Markdown draft with methodology, results, reproduce commands, and
  artifact links.
- Add optional `docpull[observability]` Raindrop support so benchmark cases can
  be emitted as metadata-only traces when `RAINDROP_WRITE_KEY` is configured.
- Add Raindrop event ids and per-case signals for benchmark failures, low
  scores, high-score cells, high-cost cells, and score-dimension warnings.
- Add Tavily and Exa live benchmark cases that normalize provider results into
  the same scored context-pack artifacts as core docpull and Parallel.
- Add `docpull benchmark quick --target-set tool-docs/provider-matrix` for
  provider-by-target matrix evals across Parallel, Exa, Tavily, Raindrop,
  DocPull, and low-cap adversarial public targets. The old `v2` target-set name
  remains a compatibility alias.
- Add weighted benchmark sub-scores for coverage, cleanliness, source fidelity,
  freshness, and density so clean and noisy targets no longer collapse to the
  same headline score.
- Add Tavily credit-to-dollar normalization with `--tavily-credit-usd` or
  `TAVILY_CREDIT_USD`, plus a weekly GitHub Actions provider-matrix benchmark.
- Add `docpull providers` for equal optional Parallel, Tavily, and Exa key
  status, durable key setup, and provider context-pack runs that can use any
  configured subset.
- Add Make targets for quick, Parallel, and Raindrop benchmark runs.

### Changed
- Let `docpull benchmark quick --provider auto/all` run all locally configured
  providers and skip missing API keys or optional SDKs without failing the core
  benchmark.
- Write `sources.md` for LLM-profile NDJSON output so core docpull packs score
  consistently with Parallel-generated context packs.
- Score Parallel Search packs from their search metadata and keep fallback-pack
  core extraction artifacts scoped to their intended output directory.

## [4.1.0] - 2026-06-07

### Added
- Add optional `docpull[parallel]` support for building Parallel Search +
  Extract context packs with local NDJSON, source Markdown, manifests, and
  workflow metadata.
- Add `docpull parallel import` for offline fixture/demo workflows and a
  checked-in Parallel context-pack example fixture.
- Add `docpull parallel demo`, backed by a packaged fixture, so the offline
  context-pack demo works from an installed wheel.
- Add a Parallel product cross-reference covering Search, Extract, Task,
  FindAll, Entity Search, Monitor, MCP, and planned follow-up workflows.
- Align Search mode choices with Parallel API docs (`turbo`, `basic`, and `advanced`) and
  request a Task text output schema for `--task-brief`.
- Add source-policy, client-model, dry-run, and local cost-guard controls for
  live Parallel context packs.
- Add broader Parallel artifact workflows for Entity Search, FindAll, TaskGroup
  batches, Monitor metadata/events, and `llms.txt`/OpenAPI API packs.
- Add `docpull parallel search-pack`, `extract-pack`, `task-pack`,
  `task-result`, and `task-events` for Search-only, known-URL Extract, and
  Task lifecycle packs.
- Add FindAll ingest, result, schema, enrich, extend, cancel, and events pack
  workflows.
- Add snapshot monitor creation, monitor source-policy/location/webhook/metadata
  controls, event-group summaries, and checked-in Parallel API-pack recipes.
- Add MCP tools for `parallel_context_pack`, `parallel_api_pack`, `pack_score`,
  and `pack_diff`, plus a built-in `parallel` source alias.
- Add `docpull pack score` and `docpull pack diff` for local pack quality checks
  and refreshed-pack comparisons.
- Add `docpull parallel auth` to check optional SDK and `PARALLEL_API_KEY`
  readiness without storing or printing secrets.
- Add raw Markdown/plain-text conversion for docs indexes such as `llms.txt`
  through the normal fetch pipeline.
- Add Parallel fetch policy, excerpt-size, and Search location controls to
  context-pack CLI and recipe workflows.
- Add Monitor list, retrieve, update, cancel, trigger, and cursor/event-group
  events pack workflows.

### Changed
- Cap `docpull parallel context-pack --extract-limit` and context-pack recipes at
  20 URLs so a single Parallel Extract request stays within the documented API
  limit.
- Make `docpull parallel taskgroup-pack --wait` poll TaskGroup status until the
  group is inactive before snapshotting run outputs.
- Treat no-content, invalid-content, HTTP-error, and save-empty skips as failures
  for `docpull --single`, so single-page agent fetches do not report empty
  output as success.
- Extend `docpull parallel run` beyond context-pack recipes so YAML/JSON recipes
  can dispatch the same Parallel pack workflows as the explicit CLI commands.
- Refresh the documented 10,000-page benchmark wall time to the latest local
  audit run.

### Security
- Route remote `docpull parallel api-pack` sources through docpull's hardened
  HTTPS-only URL validation, robots.txt check, DNS-pinned HTTP client, redirect
  revalidation, and response-size cap instead of a raw `urllib` fetch.

## [4.0.1] - 2026-06-06

A release-readiness patch that tightens the public product boundary. No runtime
API changes and no migration needed.

### Changed
- Make the Python `docpull mcp` server the only documented supported MCP path
  for agents, plugins, Claude Code, Cursor, and Claude Desktop.
- Mark the root TypeScript/Bun `mcp/` tree as an internal lab, make its package
  metadata private, and remove end-user install instructions for that path.
- Replace stale YAML example files with current CLI recipes so docs no longer
  advertise removed options such as `--sources-file`, TOON output,
  `keep_variant`, `language`, or `create_index`.
- Update website examples and performance copy to match the current CLI and
  benchmark results.

## [4.0.0] - 2026-06-04

A security + cleanup release. A multi-agent security audit closed a high-severity
SSRF and nine further findings (see Security); it ships alongside a tech-debt
cleanup that removes several unused public APIs (see Removed — the breaking
changes that make this a major release).

### Security
- **DNS-rebinding TOCTOU in the URL validator (high).** `UrlValidator.resolve_allowed_addresses()`
  resolved the hostname a second time and used that unscreened answer as the
  connect target, so a TTL-0 attacker could pass validation with a public IP and
  have the socket dialed at an internal one (e.g. cloud metadata). It now resolves
  once and returns exactly the addresses it screened.
- **Wider SSRF coverage.** Block CGNAT shared address space (`100.64.0.0/10`) and
  IPv4-mapped IPv6 forms, and strip the trailing DNS root dot (`localhost.`) before
  the localhost/suffix checks — in both the Python validator and the TypeScript MCP
  source gate. The MCP gate additionally denies wildcard DNS-rebinding hosts
  (`*.nip.io`, `*.sslip.io`, `*.xip.io`).
- **robots.txt memory-exhaustion DoS.** Cap the robots.txt body read at 512 KB,
  matching the existing sitemap limit.
- **YAML frontmatter injection.** Frontmatter list items (tags/keywords sourced from
  page JSON-LD / OpenGraph) are quoted, escaped, and stripped of CR/LF so a hostile
  page cannot inject top-level frontmatter keys.
- **Conditional-request header injection.** Cached `ETag` / `Last-Modified` values are
  stripped of CR/LF/NUL before being reused as `If-None-Match` / `If-Modified-Since`.
- **Supply chain.** Pin release tooling (`pip` / `build` / `twine`) via
  `requirements-release.txt`; drop six unused (ghost) dependencies from the MCP
  package; bump `aiohttp` to `>=3.14.0` (CVE-2026-34993, CVE-2026-47265).

### Removed
- **Unused public methods on `CacheManager`** (breaking for any external caller):
  `has_changed`, `is_fetched`, `is_failed`, `get_failed_urls`, `get_cache_stats`,
  `clear_state`, and `has_resume_data` had no callers in the library or tests.
  Incremental fetch and resume are unaffected — they use the retained
  `update_cache`, `mark_fetched`, `mark_failed`, `get_fetched_urls`,
  `get_pending_urls`, `save_/load_/clear_discovered_urls`, and `evict_expired`.
- **`StreamingDeduplicator.is_duplicate`** (breaking): unused read-only probe.
  Use `check_and_register`, whose first return value reports whether content was new.
- **`DocpullConfig.from_yaml_file`** (breaking): unused convenience wrapper.
  Use `DocpullConfig.from_yaml(path.read_text())`.

### Changed
- Internal cleanup with no API or behaviour change: removed dead code (the unused
  `concurrency` package, `logging_config`, and several private dead methods) and
  de-duplicated the discovery HTML-fetch helper and the HTTP GET/HEAD redirect
  re-validation path.

### Fixed
- **MCP indexing of large libraries.** pgvector embedding inserts are now batched
  under PostgreSQL's 32767 bind-parameter ceiling, so a library with thousands of
  chunks indexes in one transaction instead of failing.

## [3.0.2] - 2026-05-29

A small release hygiene patch for the MCP hardening release. No API changes;
no migration needed.

### Fixed
- **Runtime version now matches package metadata.** `docpull --version` and the
  default HTTP `User-Agent` report `3.0.2` instead of the stale `3.0.0` value
  that remained in `docpull.__version__` after the 3.0.1 publish.

### Tests
- **Added a stdio MCP smoke test.** The test starts `docpull mcp` through the
  official MCP client, verifies the advertised 8-tool surface, checks
  structured `list_sources` output, and confirms SSRF rejection still flows
  through a real MCP `call_tool` request.

## [3.0.1] - 2026-05-29

A security and correctness patch. No API changes; no migration needed.

### Security
- **User-defined MCP sources are validated on load.** Entries in
  `~/.config/docpull-mcp/sources.yaml` are now rejected unless the name is a
  safe identifier, the URL is HTTPS to a public host (private, loopback,
  link-local, and internal-suffix hosts are blocked), and `max_pages` is in
  range. Previously a hand-edited config could point `ensure_docs` at an
  internal address.
- **`grep_docs` bounds regex execution per line.** On top of the existing
  total wall-clock budget, each line now matches under a per-line timeout,
  closing the remaining catastrophic-backtracking (ReDoS) window for a
  pathological pattern against a single long line.

### Fixed
- **Cache timestamps are timezone-aware UTC.** Persisted timestamps (cache
  manifest, save steps, MCP metadata) use UTC consistently; legacy naive
  timestamps are parsed as UTC so cache-TTL comparisons stay deterministic
  instead of mis-expiring entries.
- **Swallowed exceptions are now logged.** robots.txt parsing, the OpenAPI
  and SPA heuristics, and link extraction log skipped or invalid input at
  debug level instead of silently dropping it.

## [3.0.0] - 2026-04-26

The deprecations 2.4 promised. Six config fields that have emitted a
`DeprecationWarning` since 2.4 are now gone, and the `naming_strategy`
literal no longer accepts the `"flat"` / `"short"` aliases that were
documented as "aliased to 'full' until 3.0".

### Breaking
- **`ContentFilterConfig` removed fields** — `language`,
  `exclude_languages`, `deduplicate`, `max_total_size`,
  `exclude_sections`. All have been no-ops since 2.4 with a
  deprecation warning on use; pydantic will now reject configs that
  set them (`model_config = {"extra": "forbid"}`). For
  `deduplicate=True`, switch to `streaming_dedup=True`. The other
  fields had no replacement because they had no effect.
- **`OutputConfig.create_index` removed** — also a no-op since 2.4.
  Drop the field from your config; nothing to migrate.
- **`OutputConfig.naming_strategy` literal narrowed** — the alias
  values `"flat"` and `"short"` (which silently behaved like `"full"`)
  are no longer accepted. Use `"full"` directly. `"hierarchical"` is
  unchanged.
- **`docpull.deprecated` logger removed** — the dedicated logger and
  the per-call `DeprecationWarning` infrastructure for the above
  fields are gone with them. Filters that targeted `docpull.deprecated`
  can be removed.

### Migration
If your config file or `DocpullConfig(...)` call sets any of the
removed fields, delete those lines. Pydantic's `forbid` policy will
otherwise raise `ValidationError` at construction time with a clear
"Extra inputs are not permitted" message naming the field.

## [2.5.1] - 2026-04-25

A small but real bugfix: the `grep_docs` → `read_doc` round-trip was
broken. `grep_docs` returned paths with the library name prepended
(e.g. `hono/middleware/basic-auth.md`), but `read_doc` joins
`library` and `path` itself, so passing a grep result verbatim
produced `hono/hono/middleware/basic-auth.md` and 404'd. The
contract advertised in the `read_doc` description ("the natural
follow-up to grep_docs: pass the library + path it returned")
didn't actually work.

### Fixed
- **`grep_docs` returns library-relative paths.** Each result in the
  structured `files` payload now has both `library` (the library
  name) and `path` (relative to the library root). Pass them
  straight into `read_doc(library=..., path=...)` — no munging.
  Human-readable text rendering still shows `library/path` as the
  qualified identifier, so existing terminal output looks identical.
- Tool descriptions for `grep_docs` and `read_doc` updated to match
  the actual contract.

### Schema
- `_GREP_DOCS_OUTPUT_SCHEMA.files.items` now requires `library` in
  addition to `path`. Existing consumers that read `path` will get
  a different (now correct) value; consumers that don't pipe grep
  results into `read_doc` are unaffected.

### Tests
- Added `test_grep_to_read_doc_roundtrip` and
  `test_grep_to_read_doc_roundtrip_with_line_slice` regression
  tests that pass `library` and `path` from grep verbatim into
  `read_doc` and assert success.
- Added `test_grep_docs_path_is_library_relative_in_subdir` to
  cover nested files.
- Updated `test_grep_docs_structured_payload` to assert the new
  `library` field and exact (not just suffix-matched) path value.

## [2.5.0] - 2026-04-25

A focused MCP-server hardening pass. Closed three exploitable security
holes in the agent-facing tools, added the missing `ToolAnnotations`
that gate Anthropic Directory submission, exposed structured output
alongside the rendered text on every tool that carries data, and
added the three tools an agent obviously wants — `read_doc` to follow
up a `grep_docs` hit, plus `add_source` / `remove_source` to manage
the user registry programmatically.

### Added
- **`read_doc(library, path, line_start?, line_end?)`** — read a
  Markdown file from a fetched library, optionally line-sliced. The
  natural follow-up after `grep_docs` returns a hit; agents no longer
  need filesystem access for surrounding context. Path is resolved
  and confirmed to stay under the library root.
- **`add_source(name, url, ...)`** — add or update a user source
  alias in the writable `sources.yaml`. Refuses to shadow a builtin
  alias unless `force=true`; URL is HTTPS-only and validated against
  the same SSRF rules as `fetch_url`. Atomic write (tmp + rename).
- **`remove_source(name, delete_cache?)`** — remove a user source
  alias and optionally its cached docs directory. Cannot remove
  builtins (suggest `add_source(force=true)` to shadow instead).
  Cache deletion does a defense-in-depth resolved-path check.
- **`ToolAnnotations` on every tool** — `readOnlyHint` /
  `destructiveHint` / `idempotentHint` / `openWorldHint` / `title`.
  Required for Anthropic Directory submission and unlocks host
  auto-approve for the four read-only tools.
- **Server `instructions`** — system-prompt hint telling agents the
  call ordering (list_sources → ensure_docs → grep_docs → read_doc).
- **Progress notifications** — `ensure_docs` forwards
  `FETCH_COMPLETED` events as MCP progress to clients that supplied
  a `progressToken` on the call.
- **Structured output** (`outputSchema` + `structuredContent`) on
  `list_sources`, `list_indexed`, `grep_docs`, `read_doc`,
  `ensure_docs`, `add_source`, `remove_source`. Clients that consume
  `structuredContent` get parseable JSON; clients that don't still
  see the rendered Markdown text.

### Fixed
- **SSRF in `fetch_url`** — schema previously accepted any string
  with no scheme/host enforcement. An agent could request
  `http://169.254.169.254/`, `http://localhost`, `file:///etc/passwd`,
  etc. Now validated upfront with the same `UrlValidator`
  (HTTPS-only, no localhost / private / link-local IPs) the crawler
  uses, instead of relying on the slow pipeline error path.
- **Path traversal in `grep_docs` / `read_doc` via `library`** —
  `docs_dir / library` did not validate `library`, so
  `library="../../etc"` walked anywhere the process could read.
  `read_doc`'s `path` arg was similarly unchecked. Both now reject
  unsafe names (`is_safe_library_name`) and `read_doc` additionally
  resolves the joined path and confirms it stays under the library
  root.
- **ReDoS in `grep_docs`** — pattern was compiled with no length
  cap and run line-by-line over every cached `.md`; Python `re`
  has no timeout knob. Now cap pattern length at 1000 chars and
  apply a 10s wall-clock budget across files.
- **`isError` flag was being silently dropped** — the previous
  `_call_tool` returned a bare `list[TextContent]`, which the SDK's
  legacy path hardcodes as `isError=False` regardless of what the
  handler intended. Every error your tools raised was being reported
  to clients as success. Now `_call_tool` returns `CallToolResult`
  directly so `isError` propagates correctly.
- **`ensure_docs` partial-fetch detection** — a crash mid-crawl
  used to leave files on disk with no meta, and the next call
  would re-fetch (correct, but wasteful) or — if a stale meta from
  a prior run was present — trust the half-fetched cache. Meta
  writes are now atomic (tmp + rename) and a `partial=true` flag
  marks half-fetches so `_cache_fresh` treats them as stale.
- **`grep_docs` honors `context > 1`** — the schema advertised
  `maximum: 3` but the implementation only ever rendered one line
  either side. Now renders up to `context` lines on each side.
- **`load_user_sources` silently swallowed YAML errors** — a typo
  in the user's `sources.yaml` produced "Unknown source" instead of
  surfacing the parse failure. Now logs a warning at WARNING level.

### Changed
- **Tighter input validation** in the MCP `_call_tool` dispatcher:
  required strings checked with `_require_str`, ints coerced with
  `_coerce_int`. Errors that used to surface as ugly
  "invalid literal for int(...)" now return clear messages naming
  the bad argument.
- **Tighter input schemas**: `https://` pattern on `fetch_url.url`,
  `enum` on `category`, regex + `maxLength` on `library` everywhere
  it appears, `maxLength: 1000` on `grep_docs.pattern`, integer
  bounds on `max_tokens` / `max_pages`.
- `_cache_fresh` now also requires the source directory to contain
  at least one `.md` file — a manually-`rm -rf`'d cache no longer
  reports as fresh.
- `_PROFILE_ALIASES` mapping deleted; `_resolve_profile` now goes
  through `ProfileName` directly, eliminating drift.
- 39 new MCP tests (61 total in `test_mcp_tools.py`, 316 in the
  full suite). Coverage includes SSRF rejection, path traversal,
  oversized regex, partial-meta freshness, structured payloads,
  and the new write tools.

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

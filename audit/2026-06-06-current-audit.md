# DocPull Current Audit - 2026-06-06

Scope: local repo at `/Users/mb1/Code/secondary/docpull`, product site `https://docpull.raintree.technology/`, PyPI package `docpull`, GitHub repo `raintree-technology/docpull`, Python MCP under `src/docpull/mcp/`, root TypeScript/Bun MCP under `mcp/`, Claude plugin docs under `plugin/`.

## Executive Summary

DocPull is a Python 3.10+ CLI/library that crawls static or server-rendered documentation over async HTTP, applies URL/robots/security policy, extracts main content, converts to Markdown-oriented records, and writes Markdown, JSON, NDJSON, or SQLite outputs. It also ships a package-installed Python stdio MCP server for agent workflows. The repository additionally contains a separate Bun/TypeScript MCP server with PostgreSQL/pgvector semantic search and a Claude Code plugin bundle.

Current version is verified as `4.0.0`:
- Local metadata: `pyproject.toml:6-7`, `src/docpull/__init__.py:17`, `python -m pip show docpull`.
- Runtime: `docpull --version` prints `docpull 4.0.0`.
- PyPI JSON and project page report latest `4.0.0`, released 2026-06-04.
- Local git log includes `a3a288e chore(release): 4.0.0 (#48)`.

The current local worktree is not clean:
- `git status --short --branch`: `main...origin/main [ahead 2, behind 1]`, untracked `.claude-plugin/plugin/`, untracked `audit/`.
- This audit did not reset, clean, or modify product code.

Overall assessment before the follow-up fixes: the Python package was in good shape for a CLI/library/security posture, with green tests/lint/type/dependency audit in this environment. The main risks were claim drift and product-boundary confusion: stale published README copy about `docpull-mcp`, the root TypeScript MCP tree pointing to a public repo that returned 404, a mirror-profile naming mismatch, security skips returning exit code 0 for invalid targets, and a few docs/product claims that were broader than verified behavior. The concrete items from that list have now been fixed locally; publishing a new package release is still required to replace the older PyPI long description.

## Resolution Update

The implementation pass after this audit closed the concrete claim/implementation gaps called out below:

- Mirror profile now defaults to hierarchical output paths while preserving explicit user overrides.
- `--single` now exits nonzero for URL-validation and robots-policy skips.
- `--insecure-tls` help now accurately says the flag is deprecated and rejected.
- README, plugin docs, root `mcp/` docs/package metadata, and web profile copy no longer claim unavailable mirrors or fail-loud defaults that are not true.
- Sphinx wording now cites the actual detection basis: generator metadata and Read the Docs hosts.
- Robots `Crawl-delay` is now applied to the start host's rate limiter using the stricter of configured `rate_limit` and robots delay.
- Regression coverage was added for mirror profile defaults/overrides, CLI help text, invalid single-URL exit behavior, and crawl-delay limiter wiring.

Post-fix gates passed: `pytest -q` (`359 passed`), `ruff check .`, `mypy src/docpull`, `bandit -c pyproject.toml -r src/docpull`, `pip-audit`, and web `npm run lint && npm run typecheck && npm run build`.

## Baseline Commands

Requested command `python -m venv .venv` failed because `python` is not on PATH in this shell:

```text
zsh:1: command not found: python
```

Equivalent setup with `python3` succeeded:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[all,dev]'
```

Baseline verification:

| Command | Exit | Result |
|---|---:|---|
| `python -m pip show docpull` | 0 | Editable `docpull==4.0.0`; location `.venv/lib/python3.11/site-packages`; editable project location is repo root. |
| `docpull --version` | 0 | `docpull 4.0.0`. |
| `docpull --help` | 0 | Shows profiles `rag, mirror, quick, llm`; formats `markdown,json,ndjson,sqlite`; auth flags; cache; chunking; `--skill`; `--require-pinned-dns`. |
| `docpull --doctor` | 0 | Core and optional deps OK; network connectivity OK; output dir writable. |
| `pytest -q` | 0 | `353 passed in 10.48s`. |
| `ruff check .` | 0 | All checks passed. |
| `mypy src/docpull` | 0 | Success, 61 source files. |
| `bandit -r src/docpull` | 1 | 8 low-severity `B101 assert_used` findings in `fetcher.py` and `convert.py`; `pyproject.toml:182-197` documents a repo policy to skip B101, but the requested command did not pass `-c pyproject.toml`. |
| `pip-audit` | 0 | No known vulnerabilities found. |

The intended Bandit command for repo policy should be `bandit -c pyproject.toml -r src/docpull`.

## Claim Matrix

| Claim | Source | Category | Verification | Status | Evidence / Risk / Fix |
|---|---|---|---|---|---|
| Browser-free async HTTP crawler for static/server-rendered docs | README, PyPI, site | CLI/functionality | Static + runtime | Verified | `AsyncHttpClient` in `src/docpull/http/client.py`; site says browser-free; `docpull https://example.com --single` succeeded. |
| Clean Markdown with YAML frontmatter source URL | README/site | Output | Runtime | Verified | Markdown output contained `title`, `source`, `headings`, `crawled_at`; save path `index.md`. |
| Formats `markdown`, `json`, `ndjson`, `sqlite` | README/CLI | Output | Runtime | Verified | Representative `https://example.com --single` produced `index.md`, `documents.json`, `documents.ndjson`, and `documents.db`; all exit 0. |
| Every output format writes `corpus.manifest.json` | README | Output | Runtime + code | Verified for markdown/json/ndjson/sqlite | Runtime markdown emitted manifest; output tests cover json/ndjson/sqlite; savers use `CorpusManifest`. |
| `--stream` streams NDJSON to stdout | README/CLI | Agent output | Runtime | Verified | `docpull https://example.com --single --profile llm --stream --quiet` emitted one NDJSON line with `chunk_id`, `token_count`, `chunk_index`. |
| LLM profile is chunked NDJSON metadata output | README/profiles | Output/profile | Runtime + code | Verified | `src/docpull/models/profiles.py:47-64` sets format `ndjson`, rich metadata, max tokens 4000, emit chunks; runtime confirmed chunk record. |
| LLM profile is fail-loud on JS-only pages | Live site said "fail-loud"; profile comment says JS-only skipped by default | Product/profile | Code | Resolved post-audit | Web copy now says LLM skips JS-only pages unless strict mode is enabled. |
| Mirror profile defaults to hierarchical naming | CLI help says mirror defaults hierarchical | CLI/profile | Runtime | Resolved post-audit | Mirror profile now sets `output.naming_strategy = "hierarchical"` while explicit user overrides still win. |
| `--single` fetches one URL without discovery | README/CLI | CLI | Runtime + code | Verified | `Fetcher.fetch_one()` bypasses discovery in `src/docpull/core/fetcher.py:515-550`; runtime examples succeeded. |
| `--include-paths` / `--exclude-paths` filter discovery | README/CLI/site | Discovery | Static/tests | Verified by implementation, not separately runtime-tested in this pass | CLI maps to `PatternFilter`; `Fetcher.__aenter__` creates it at `src/docpull/core/fetcher.py:414-420`. |
| Sitemap discovery, sitemap index recursion, XXE protection | README/security/code | Discovery/security | Static/tests | Verified by code/tests | `src/docpull/discovery/sitemap.py:9-43`, `149-186`; `defusedxml`, size/depth limits. |
| Mandatory robots.txt compliance | README/security/site | Security | Static/tests | Verified | `ValidateStep` receives `RobotsChecker`; robots checker fail-closes on errors, allows missing 4xx, caps 512 KB in `src/docpull/security/robots.py`. |
| Crawl-delay handling | User audit checklist | Discovery | Static + tests | Resolved post-audit | `Fetcher._apply_robots_crawl_delay()` now applies robots delay to the start host's rate limiter using the stricter delay. |
| HTTPS-only, SSRF, DNS-rebinding protection | README/PyPI/security | Security | Static + runtime | Verified | `UrlValidator` blocks non-HTTPS/private/local/CGNAT/IPv4-mapped; `_ValidatedResolver` pins allowed addresses; runtime `http://example.com` and `https://localhost` were skipped with validation reasons. |
| Unsafe URL should fail command | Security expectation for agent tooling | DX/security | Runtime | Resolved post-audit | `--single` now returns nonzero for URL-validation and robots-policy skips. |
| `--insecure-tls` disables TLS | CLI help said "Disable TLS certificate verification (unsafe)" | Security/CLI | Runtime + code | Resolved post-audit | Help now says the flag is deprecated and rejected. |
| Proxy delegates DNS pinning unless `--require-pinned-dns` | README/security | Security | Runtime + code | Verified | Runtime proxy + require pinned DNS exits 1; code warns in `AsyncHttpClient.__aenter__`. |
| Auth headers stripped on cross-origin redirects | README/security | Security | Static/tests | Verified | `src/docpull/http/client.py:209-235`; tests cover off-scope stripping. |
| Rich metadata extraction | README/site | Metadata | Static/tests | Verified by code, partial runtime | `MetadataStep` + `metadata_extractor.py`; runtime basic page metadata present; rich OG/JSON-LD not separately fixture-tested in this pass. |
| Code blocks, language hints, tables, images, cookie banners | Site FAQ | Conversion | Static/tests | Partially verified | Conversion stack has extractor/converter and tests, but this pass did not exhaustively fixture each FAQ item. Keep claim but add explicit fixture coverage. |
| Framework extractors: Next.js, Mintlify, OpenAPI, Docusaurus, Sphinx | README | Extraction | Static/tests | Partial | Next/Mintlify/OpenAPI code is clear in `special_cases.py`; Docusaurus is detection/tagging only via generic fallback; Sphinx was not confirmed in inspected code. Add explicit Sphinx evidence/test or soften claim. |
| JS-only SPA detected and skipped / strict error | README/site | Extraction | Static/code | Verified by code, runtime not fixture-tested | `ConvertStep._handle_empty_content()` and `looks_like_spa` path. |
| Python API `fetch_one`, `Fetcher`, `DocpullConfig`, async events | README | API | Static + import | Verified | Exported in `src/docpull/__init__.py:19-33`; `fetch_one()` sync wrapper at `src/docpull/core/fetcher.py:957-990`. |
| Python package is typed | PyPI classifier | Packaging | Static | Verified | `src/docpull/py.typed`; `pyproject.toml:140-141`; mypy passes. |
| MCP server ships in Python package | README/plugin | MCP | Runtime + tests | Verified | `docpull mcp --help` works; `tests/test_mcp_server.py` verifies 8 tools and SSRF rejection. |
| MCP tools: 8 total, annotations, structured output | README | MCP | Static/tests | Mostly verified | Server declares 8 tools with annotations/output schemas; `fetch_url` intentionally omits output schema. Wording "all tools that carry data" should be "schema-backed tools" or add schema to `fetch_url`. |
| User sources in `~/.config/docpull-mcp/sources.yaml` | README/MCP | MCP | Static | Verified | `src/docpull/mcp/sources.py` defaults and loaders. |
| Plugin cache path is `$XDG_DATA_HOME/docpull/docs` | `plugin/README.md:63-65` | Plugin docs | Static | Resolved post-audit | Plugin README now uses `docpull-mcp/docs`. |
| Plugin prerequisite "2.5.0 or newer" | `plugin/README.md:27` | Plugin docs | Static | Resolved post-audit | Plugin README now says `4.0.0 or newer`. |
| Root `mcp/` mirror exists at `raintree-technology/docpull-mcp` | Published PyPI README and old `mcp/README.md`/package metadata | Packaging/docs | Live HTTP | Resolved locally; pending release | Local root MCP docs/package metadata now point to the in-repo `mcp/` directory under `raintree-technology/docpull`; PyPI will remain stale until the next release. |
| 10k pages in about 27s, 28 MB RSS | README/site | Performance | Static/tests | Not rerun; benchmark exists | `tests/benchmarks/test_10k_pages.py` contains gated benchmark and assertions. This audit did not run 10k benchmark to avoid heavier crawl. Treat published numbers as benchmark-derived but not freshly measured. |
| No telemetry/local-first/no remote services | README/plugin/site | Privacy/product | Static | Mostly verified for Python CLI | Python CLI makes requested HTTP calls only; root TypeScript MCP uses OpenAI embeddings when semantic indexing is enabled, so product copy should keep Python package and TS MCP boundaries clear. |

## Architecture Map

Top-level structure:
- `src/docpull/`: Python package, public API, CLI, security, HTTP, discovery, conversion, pipeline, cache, MCP.
- `tests/`: unit/e2e/security/MCP/output tests.
- `tests/benchmarks/`: synthetic performance benchmarks.
- `mcp/`: separate Bun/TypeScript MCP server with PostgreSQL/pgvector/OpenAI semantic search.
- `plugin/`: Claude Code plugin documentation; untracked `.claude-plugin/plugin/` appears to contain built plugin output.
- `web/`: Next.js product site.
- `.github/`: CI, publish, CodeQL, metrics, security workflows.

Python package structure and responsibilities:
- `cli.py`: argparse surface, maps CLI flags into `DocpullConfig`, dispatches normal fetch or `docpull mcp`.
- `models/config.py`: Pydantic config objects: crawl, content filter, output, network, auth, performance, cache.
- `models/profiles.py`: profile defaults for `rag`, `mirror`, `quick`, `llm`, `custom`.
- `core/fetcher.py`: orchestration; builds validator, robots checker, HTTP client, pipeline, discoverers; exposes `run()`, `discover()`, `fetch_one()`, sync wrappers.
- `security/url_validator.py`: HTTPS policy, hostname/IP validation, DNS screening, connect-time address list.
- `security/robots.py`: robots.txt fetch/parse/cache with pinned HTTPS and fail-closed errors.
- `http/client.py`: aiohttp client, validated resolver, redirect validation, auth scoping, retry/timeout/content caps.
- `discovery/`: sitemap and link crawling, pattern/domain/seen filters, composite fallback.
- `conversion/`: main content extraction, html2text conversion, trafilatura adapter, framework special cases, chunking.
- `metadata_extractor.py`: title/description/OpenGraph/JSON-LD/microdata metadata.
- `pipeline/base.py`: `PageContext`, `FetchPipeline`, step protocol.
- `pipeline/steps/`: validate, fetch, metadata, convert, dedup, chunk, save markdown/json/ndjson/sqlite.
- `cache/`: HTTP cache state, frontier/resume, streaming dedup.
- `mcp/`: package-shipped Python stdio MCP server, tools, source registry.

Runtime data flow:

```text
CLI/API input URL
  -> DocpullConfig + profile defaults
  -> Fetcher.__aenter__
  -> UrlValidator + RobotsChecker + AsyncHttpClient
  -> CompositeDiscoverer
       -> SitemapDiscoverer from robots Sitemap or guessed sitemap locations
       -> LinkCrawler fallback/static link extraction
  -> FetchPipeline per URL
       -> ValidateStep: URL policy + robots + cache existing
       -> FetchStep: HTTP GET + conditional headers/cache response handling
       -> MetadataStep: title/OG/JSON-LD/microdata
       -> ConvertStep: source detection -> special cases/trafilatura/generic -> frontmatter
       -> DedupStep: optional streaming duplicate skip
       -> ChunkStep: optional token chunks
       -> SaveStep / JsonSaveStep / NdjsonSaveStep / SqliteSaveStep
  -> CorpusManifest finalization + cache/frontier flush
  -> FetchEvent stream + FetchStats
```

Trust boundaries:
- User/agent-provided URL crosses into `UrlValidator`; default allows only HTTPS and public network destinations.
- DNS is resolved by `UrlValidator.resolve_allowed_addresses()` and reused by `_ValidatedResolver` when no proxy is configured.
- Proxy mode shifts DNS trust to the proxy; `--require-pinned-dns` rejects that mode.
- Redirect targets are revalidated and sensitive auth headers are stripped across origin changes.
- robots.txt and sitemaps are untrusted network inputs: robots has body cap; sitemap uses `defusedxml`, size/depth caps, URL validation.
- HTML/JSON-LD/OpenAPI inputs are untrusted: conversion and metadata extraction should never execute scripts; frontmatter list values are sanitized.
- Output paths are derived from sanitized URL segments and validated under base output dir.
- MCP tools take agent-controlled strings; Python MCP validates URLs, source names, library names, regex size/time, and read paths.
- Root TypeScript MCP adds database and OpenAI API trust boundaries separate from Python package.

Extension points:
- Add `SpecialCaseExtractor` implementations to `DEFAULT_CHAIN`.
- Swap extractor via `--extractor trafilatura`.
- Add output savers through pipeline step pattern.
- Add profiles in `models/profiles.py`.
- Add MCP tools in `src/docpull/mcp/server.py` + `tools.py`.
- Add discovery sources/extractors through discoverer protocols and link extractor protocols.

Dead/duplicated systems:
- Python MCP and root TypeScript MCP are separate MCP products with overlapping names/source concepts but different persistence and search models.
- Root `mcp/` still documents clone of `docpull-mcp` and metadata points to a GitHub repo currently returning 404.
- CLI still exposes `--insecure-tls` but rejects it.
- Existing untracked `audit/*.md` files are stale relative to this run; they mention an import break that is no longer present.

## Functional Verification

Verified locally:
- Install editable with all/dev extras under Python 3.11.
- CLI `--version`, `--help`, `--doctor`.
- Full test suite: 353 passed.
- Static quality: ruff/mypy pass.
- Dependency audit: no known vulnerabilities.
- Bandit: only B101 asserts surfaced under the raw requested command.
- `docpull mcp --help`.
- `--single` markdown/json/ndjson/sqlite against `https://example.com`.
- `--profile llm --stream` emitted chunked NDJSON to stdout.
- Security rejection messages for `http://example.com`, `https://localhost`, `--insecure-tls`, and proxy + `--require-pinned-dns`.

Not exhaustively rerun in this pass:
- 10k benchmark.
- Every framework extractor fixture.
- Auth against a real protected docs site.
- Invalid cert and 404 live cases.
- Proxy success path.
- Complete include/exclude/depth/backpressure behavior outside tests.
- Root TypeScript MCP runtime with PostgreSQL/OpenAI.
- Product-site browser interaction beyond HTML fetch.

## Issue-Ready Findings

### P1 - Resolved Locally: public PyPI README still claims a `docpull-mcp` mirror that returns 404

Severity: High  
Confidence: High  
Evidence:
- PyPI project page for `4.0.0` says the root `mcp/` tree is mirrored to `raintree-technology/docpull-mcp`.
- `curl -I -L https://github.com/raintree-technology/docpull-mcp` returned HTTP 404.
- Local `mcp/README.md:17-18` tells users to `git clone https://github.com/raintree-technology/docpull-mcp`.
- Local `mcp/package.json:35-38` points repository metadata at the same URL.
- Local top-level `README.md` has already been softened to say no public mirror is claimed, but that fix is not reflected in the published PyPI long description.

Reproduction:
1. Open `https://pypi.org/project/docpull/`.
2. Search for `docpull-mcp`.
3. Open `https://github.com/raintree-technology/docpull-mcp`.

Expected: linked mirror exists, or docs clearly say the mirror is private/unavailable.  
Actual: public package metadata points users to a 404.  
Resolution: local `mcp/README.md` and `mcp/package.json` now point to the in-repo `mcp/` directory under the main `docpull` repository. A package release is still needed to replace the older PyPI long description.

### P1 - Resolved: mirror profile naming claim was false

Severity: Medium  
Confidence: High  
Evidence:
- CLI help says mirror profile defaults to hierarchical naming.
- `src/docpull/models/profiles.py:22-38` intentionally does not set `output.naming_strategy`.
- Runtime check returned `naming_strategy full` for `Fetcher(DocpullConfig(profile=ProfileName.MIRROR))`.

Reproduction:
```bash
. .venv/bin/activate
python - <<'PY'
from docpull import DocpullConfig, Fetcher, ProfileName
print(Fetcher(DocpullConfig(url="https://example.com", profile=ProfileName.MIRROR)).config.output.naming_strategy)
PY
```

Expected: either `hierarchical` or docs do not claim mirror defaults hierarchical.  
Actual: `full`.  
Resolution: mirror now defaults to hierarchical output paths, with a regression test proving explicit `full` overrides still win.

### P1 - Resolved: security validation skips returned exit code 0 in `--single`

Severity: Medium  
Confidence: High  
Evidence:
- `docpull http://example.com --single` exits 0 with `Skipped: URL validation failed: Scheme 'http' not allowed`.
- `docpull https://localhost --single` exits 0 with `Skipped: URL validation failed: Localhost URLs not allowed`.
- `src/docpull/cli.py:552-558` returns 0 on any `ctx.should_skip`.

Reproduction:
```bash
docpull http://example.com --single
echo $?
docpull https://localhost --single
echo $?
```

Expected: security-policy rejections should be machine-visible failures for agent/CI callers, at least in `--single` mode.  
Actual: exit code 0.  
Resolution: `--single` now returns nonzero for URL-validation and robots-policy skips.

### P2 - Resolved: `--insecure-tls` help advertised unsupported behavior

Severity: Medium  
Confidence: High  
Evidence:
- `docpull --help` says `--insecure-tls Disable TLS certificate verification (unsafe)`.
- `src/docpull/cli.py:466-471` immediately rejects it.
- `src/docpull/models/config.py:293-298`, `src/docpull/http/client.py:159-160`, and `src/docpull/security/robots.py:124-125` reject insecure TLS.

Reproduction:
```bash
docpull https://example.com --single --insecure-tls
```

Expected: help says the flag is deprecated/rejected or the flag is removed.  
Actual: help says it disables TLS verification, then command exits 1.  
Resolution: help text now states the flag is deprecated and rejected.

### P2 - Resolved: plugin README cache path was wrong for Python MCP

Severity: Medium  
Confidence: High  
Evidence:
- `plugin/README.md:63-65` says fetched docs live under `$XDG_DATA_HOME/docpull/docs` / `~/.local/share/docpull/docs`.
- Python MCP source defaults use `docpull-mcp/docs` (`src/docpull/mcp/sources.py` via `default_docs_dir()`; observed in code inspection).

Reproduction:
1. Read `plugin/README.md`.
2. Inspect `src/docpull/mcp/sources.py`.

Expected: plugin docs match actual MCP cache path.  
Actual: docs point users to a different directory.  
Resolution: plugin README now documents `docpull-mcp/docs`.

### P2 - Resolved: product/site "fail-loud" wording conflicted with LLM profile default

Severity: Low to Medium  
Confidence: High  
Evidence:
- Live site says LLM is "fail-loud on JS-only pages".
- `src/docpull/models/profiles.py:54-57` sets `strict_js_required=False`.

Reproduction:
1. Inspect product-site rendered HTML or `web` source.
2. Inspect `ProfileName.LLM` defaults.

Expected: product copy matches default profile behavior.  
Actual: strict JS failure requires `--strict-js-required`; LLM profile skips by default.  
Resolution: web profile copy now says LLM skips JS-only pages unless strict mode is enabled.

### P2 - Resolved: Crawl-delay was discoverable but not clearly enforced

Severity: Low  
Confidence: Medium  
Evidence:
- `RobotsChecker.get_crawl_delay()` exists in `src/docpull/security/robots.py:337-361`.
- No inspected path showed it being applied to `PerHostRateLimiter` or crawl scheduling.

Reproduction:
1. Add a fixture robots.txt with `Crawl-delay`.
2. Crawl two pages and measure inter-request timing.

Expected: if crawl-delay is a claimed behavior, requests honor it.  
Actual: unverified; implementation path not obvious.  
Resolution: `Fetcher._apply_robots_crawl_delay()` now wires robots `Crawl-delay` into the start host's rate limiter and tests cover both robots-slower and user-slower cases.

## Implementation Plan

1. Docs/package alignment patch:
- Fix local `mcp/README.md` and `mcp/package.json` mirror references.
- Fix plugin README cache path and version prerequisite.
- Fix `--insecure-tls` help.
- Fix LLM fail-loud wording.
- Fix mirror profile wording or behavior.

2. CLI correctness patch:
- Make `--single` security validation failures return nonzero.
- Add tests for unsafe URL exit codes, `--insecure-tls`, and proxy + `--require-pinned-dns`.
- Add a smoke test matrix for `--version`, `--help`, `--doctor`, `docpull mcp --help`.

3. Profile/output contract patch:
- Decide mirror naming semantics and lock with tests.
- Add profile-default tests that assert `rag`, `llm`, `mirror`, `quick` config expansions.
- Add manifest schema snapshot tests for all formats.

4. Security hardening follow-up:
- Run `bandit -c pyproject.toml -r src/docpull` in CI/docs instead of raw command.
- Add tests for crawl-delay if claimed, symlinked output/cache directories, decompression/content-size behavior, and malformed metadata/frontmatter injection.

5. Product expansion:
- Make Python MCP the default public MCP path and label root TS MCP as experimental/private until the repository and install path are real.
- Add optional SQLite FTS for local search before committing to pgvector/OpenAI semantic search as the main path.
- Add verified framework fixtures for Sphinx, MkDocs, VitePress, Starlight, GitBook, ReadMe.io, Redoc, Scalar.
- Add auth-gated docs mode with host allowlists, credential scoping, secret redaction, and audit logs.
- Consider an optional JS renderer adapter behind an explicit extra and policy gate; keep browser-free default.

## Audit Bottom Line

DocPull 4.0.0 is locally runnable and substantially better than the stale untracked audit notes suggest. The Python package clears the main gates and has meaningful security architecture. The next highest-leverage work is not a rewrite; it is tightening public claims, release metadata, and agent-facing failure semantics so the project presents the same contract across README, PyPI, website, CLI, MCP, plugin, and code.

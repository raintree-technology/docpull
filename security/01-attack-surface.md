# Attack Surface Map -- docpull

**Audit date:** 2026-05-22
**Auditor:** Codex hardening pass after white-box recon
**Scope:** `/Users/mb1/Code/secondary/docpull`
**Status:** Audit findings triaged; high-signal bypasses, ignores, CI gaps, and dependency findings remediated locally.

---

## 1. Stack Summary

| Layer | Technology | Version / Notes |
|---|---|---|
| **Language (core)** | Python 3.10+ | CLI tool + library |
| **Language (MCP)** | TypeScript / Bun | MCP server for LLM tool integration |
| **Language (web)** | TypeScript / Next.js | Static marketing site (no API routes) |
| **HTTP client** | `aiohttp` >= 3.9.0 | Async, with custom resolver |
| **HTTP client (sync)** | `http.client` (stdlib) | Used only for robots.txt fetches |
| **HTML parser** | `beautifulsoup4` >= 4.12.0 | `html.parser` backend |
| **Markdown converter** | `html2text` >= 2020.1.16 | |
| **XML parser** | `defusedxml` >= 0.7.1 | Sitemaps only |
| **Structured data** | `extruct` >= 0.15.0 | OG / JSON-LD / microdata extraction |
| **Config / validation** | `pydantic` >= 2.0 | Strict models with `extra = "forbid"` |
| **YAML** | `pyyaml` >= 6.0 | Uses `yaml.safe_load` only |
| **DB (MCP)** | PostgreSQL + pgvector | Via `pg` node driver, parameterized queries |
| **Embeddings** | OpenAI `text-embedding-3-small` | MCP ingestion pipeline |
| **MCP framework** | `@modelcontextprotocol/sdk` | Stdio transport (no HTTP listener) |
| **Process spawn** | `child_process.spawn` | MCP server calls `docpull` CLI |
| **Proxy support** | `aiohttp-socks` (optional) | |
| **Input validation** | `zod` (MCP), Pydantic (Python) | |

---

## 2. Component Architecture

```
CLI user --> argparse (cli.py)
              |
              v
         DocpullConfig (pydantic, strict)
              |
              v
         Fetcher (async context manager)
           |-- UrlValidator (SSRF protection)
           |-- RobotsChecker (compliance, pinned HTTPS)
           |-- AsyncHttpClient (aiohttp, validated resolver)
           |-- CompositeDiscoverer
           |     |-- SitemapDiscoverer (defusedxml)
           |     `-- LinkCrawler (BeautifulSoup)
           `-- FetchPipeline
                 |-- ValidateStep
                 |-- FetchStep
                 |-- MetadataStep (extruct)
                 |-- ConvertStep (html2text)
                 |-- DedupStep (optional)
                 `-- SaveStep / JsonSaveStep / SqliteSaveStep

MCP Server (Bun, stdio) --> ensure_docs --> spawn("docpull", [...args])
                         --> search_docs --> pgvector semantic search
                         --> grep_docs   --> ILIKE pattern match
                         --> list_sources / list_indexed

Web frontend --> Next.js static site (no API routes, no user input)
```

---

## 3. Endpoint / Entry-Point Table

### 3.1 Python CLI (argparse)

| Entry point | Handler | Auth | Accepted inputs |
|---|---|---|---|
| `docpull <url>` | `cli.py:main` | None (local CLI) | URL (positional), all flags below |
| `--profile {rag,mirror,quick}` | `cli.py:50` | None | Enum choice |
| `--output-dir PATH` | `cli.py:100` | None | Filesystem path |
| `--format {markdown,json,sqlite}` | `cli.py:107` | None | Enum choice |
| `--max-pages N` | `cli.py:118` | None | int |
| `--max-depth N` | `cli.py:122` | None | int |
| `--include-paths / --exclude-paths` | `cli.py:140` | None | Glob patterns |
| `--proxy URL` | `cli.py:178` | None | URL string (SOCKS/HTTP) |
| `--user-agent STRING` | `cli.py:182` | None | Arbitrary string |
| `--insecure-tls` | `cli.py:186` | None | **Rejected at runtime** (always enforces TLS) |
| `--auth-bearer TOKEN` | `cli.py:202` | None | Bearer token (env var expansion supported) |
| `--auth-basic USER:PASS` | `cli.py:207` | None | Basic auth creds (env var expansion) |
| `--auth-cookie COOKIE` | `cli.py:215` | None | Cookie string (env var expansion) |
| `--auth-header NAME VALUE` | `cli.py:219` | None | Custom header name + value |
| `--cache / --cache-dir / --cache-ttl` | `cli.py:228` | None | Path, int |
| `--resume` | `cli.py:253` | None | Flag (requires --cache) |
| `DocpullConfig.from_yaml(str)` | `config.py:335` | None | YAML string (safe_load) |
| `DocpullConfig.from_yaml_file(path)` | `config.py:342` | None | Filesystem path |

### 3.2 MCP Server Tools (stdio, no HTTP)

| Tool | Handler | Auth | Inputs (zod validated) |
|---|---|---|---|
| `ensure_docs` | `server.ts:318` | Local MCP client | `source: string`, `force?: bool`, `index?: bool` |
| `list_sources` | `server.ts:440` | Local MCP client | `category?: string` |
| `search_docs` | `server.ts:478` | Local MCP client + requires DB+OpenAI | `query: string(2..500)`, `library?: string`, `limit?: int(1..50)` |
| `grep_docs` | `server.ts:549` | Local MCP client + requires DB | `pattern: string(2..200)`, `library?: string`, `limit?: int(1..20)` |
| `list_indexed` | `server.ts:611` | Local MCP client + requires DB | (none) |

### 3.3 Web Frontend

| Route | Handler | Auth | Inputs |
|---|---|---|---|
| `/` | `web/app/page.tsx` | Public | None (static marketing page, no forms, no API) |

---

## 4. Sources of Untrusted Input

| Source | Entry file:line | Trust level | Notes |
|---|---|---|---|
| **CLI `url` argument** | `cli.py:72` | Untrusted | User-supplied URL, validated by UrlValidator |
| **CLI auth flags** | `cli.py:200-224` | User-trusted | Tokens/passwords from CLI args, env var expansion |
| **CLI `--output-dir`** | `cli.py:100` | User-trusted | Filesystem path from local user |
| **CLI `--proxy`** | `cli.py:178` | User-trusted | Proxy URL, passed directly to aiohttp |
| **CLI `--user-agent`** | `cli.py:182` | User-trusted | Injected into HTTP headers |
| **YAML config files** | `config.py:335-345` | User-trusted | Loaded with `yaml.safe_load` |
| **HTTP responses (HTML body)** | `pipeline/steps/fetch.py:115` | Untrusted | Parsed by BeautifulSoup + html2text |
| **HTTP response headers** | `client.py:349-397` | Untrusted | ETag, Last-Modified, Content-Type, Location, Retry-After |
| **robots.txt content** | `robots.py:152-156` | Untrusted | Parsed by stdlib `RobotFileParser` |
| **Sitemap XML content** | `sitemap.py:162` | Untrusted | Parsed by `defusedxml.ElementTree` |
| **Structured data in HTML** | `metadata_extractor.py:60-86` | Untrusted | Parsed by `extruct` (OG, JSON-LD, microdata) |
| **DNS resolution results** | `url_validator.py:160` | Untrusted | Validated against private IP ranges |
| **Redirect Location headers** | `client.py:350-354` | Untrusted | Re-validated by UrlValidator on each hop |
| **MCP `source` parameter** | `server.ts:318` | Semi-trusted (local MCP) | Looked up against config, direct URLs rejected |
| **MCP `query` / `pattern` params** | `server.ts:490,570` | Semi-trusted | Zod validated; `pattern` used in ILIKE |
| **`sources.yaml` user config** | `server.ts:135-155` | User-trusted | Parsed by `yaml` npm package |
| **Environment variables** | `config.py:149-168`, `db.ts:11-25` | System-trusted | `DATABASE_URL`, `OPENAI_API_KEY`, `DOCS_DIR` |

---

## 5. Sinks (Dangerous Operations)

### 5.1 Outbound HTTP Requests (SSRF candidates)

| Sink | File:line | Input source | Guard |
|---|---|---|---|
| `aiohttp.session.get(url)` | `client.py:341` | Discovered URLs, redirects | `UrlValidator` + `_ValidatedResolver` at connect-time |
| `aiohttp.session.head(url)` | `client.py:466` | URL from caller | `UrlValidator._validate_url()` per request |
| `http.client.HTTPSConnection` | `robots.py:228` | robots.txt URL + redirects | `_PinnedHTTPSConnection` with validated IPs |
| `openai.embeddings.create()` | `ingest.ts:207`, `server.ts:505` | Chunk content | API key from env, content from local files |

### 5.2 File System Writes

| Sink | File:line | Input source | Guard |
|---|---|---|---|
| `Path.write_text(content)` | `pipeline/steps/save.py:116` | Converted markdown from fetched HTML | `_validate_output_path()` checks `relative_to(base_dir)` |
| `sqlite3.connect(path)` then `INSERT` | `save_sqlite.py:62-76` | Fetched URL/content/metadata | Path derived from config, parameterized SQL |
| `json.dump(doc)` via temp file | `save_json.py:117-119` | Fetched content | Atomic rename via `os.replace` |
| `json.dump(manifest)` | `cache/manager.py:124` | URLs, checksums, timestamps | Path from config |
| `json.dump(state)` | `cache/manager.py:155` | URL lists | Path from config |
| `writeFileSync(metaPath, ...)` | `server.ts:188` | Fetch metadata | Path from `META_DIR` constant |

### 5.3 Process Execution

| Sink | File:line | Input source | Guard |
|---|---|---|---|
| `spawn("docpull", args)` | `server.ts:263` | `url` from resolved source config, `outputDir`, `maxPages` | Source must be in config (direct URLs rejected by `resolveConfiguredSource`). **But** the URL value comes from `sources.yaml` or built-in config -- not directly from MCP input. |

### 5.4 SQL / Database Queries

| Sink | File:line | Input source | Guard |
|---|---|---|---|
| `client.query(INSERT ... VALUES ...)` | `db.ts:138-141` | Library name, file path, content, embeddings | Parameterized queries (`$1`, `$2`, ...) |
| `client.query(DELETE ... WHERE library = $1)` | `db.ts:158-160` | Library name | Parameterized |
| `p.query(SELECT ... WHERE embedding <=> $1)` | `db.ts:184-195` | Query embedding array | Parameterized; embedding string built from `queryEmbedding.join(",")` |
| `p.query(SELECT ... WHERE content ILIKE $1)` | `db.ts:232-239` | `pattern` from MCP input | Parameterized with `%${pattern}%` wrapping |
| `sqlite3 INSERT OR IGNORE` | `save_sqlite.py:103-113` | URL, title, markdown, metadata | Parameterized (`?` placeholders) |

### 5.5 HTML Parsing / Template Interpolation

| Sink | File:line | Input source | Guard |
|---|---|---|---|
| `BeautifulSoup(html, "html.parser")` | `extractor.py:143`, `crawler.py:89` | Fetched HTML from remote servers | No explicit sanitization (output is markdown, not re-rendered as HTML) |
| `html2text.handle(html)` | `markdown.py:123` | Extracted HTML content | Converts to markdown (strips HTML) |
| `extruct.extract(html)` | `metadata_extractor.py:63` | Fetched HTML | `errors="ignore"` |
| YAML frontmatter interpolation | `markdown.py:178-180` | Page title, description | Quotes escaped with `replace('"', '\\"')` |

### 5.6 Deserialization

| Sink | File:line | Input source | Guard |
|---|---|---|---|
| `yaml.safe_load(yaml_str)` | `config.py:339` | User-provided YAML config | Safe loader (no code execution) |
| `parseYaml(readFileSync(...))` | `server.ts:146` | `sources.yaml` user config file | `yaml` npm package (safe by default) |
| `json.load(f)` | `cache/manager.py:113,139,440` | Local cache files | Controlled paths |
| `JSON.parse(readFileSync(...))` | `server.ts:179` | Local meta files | Controlled paths |
| `defusedxml.ElementTree.fromstring(content)` | `sitemap.py:162` | Remote sitemap XML | defusedxml prevents XXE |

---

## 6. Existing Defenses Observed

### 6.1 SSRF Protection (strong, layered)

- **UrlValidator** (`security/url_validator.py`): Blocks private IPs, loopback, link-local, reserved, multicast, unspecified, site-local IPv6. Checks both literal IP and DNS-resolved addresses.
- **DNS rebinding protection**: `_ValidatedResolver` (`client.py:31-79`) validates at connect time inside aiohttp's resolver, not just before the request. This closes the TOCTOU gap where DNS could change between validation and connection.
- **Pinned HTTPS for robots.txt**: `_PinnedHTTPSConnection` (`robots.py:29-49`) resolves through the validator and pins the IP for the TLS connection.
- **Redirect validation**: Every redirect hop is re-validated (`client.py:354`). Sensitive headers (Authorization, Cookie) are stripped on cross-origin redirects (`client.py:179-194`).
- **Auth scope restriction**: Auth headers are only sent to the origin host (`client.py:196-205`).
- **HTTPS-only by default**: `allowed_schemes={"https"}` (`fetcher.py:186`).

### 6.2 TLS Enforcement

- `insecure_tls=True` raises `ValueError` in both `AsyncHttpClient` (`client.py:155-156`) and `RobotsChecker` (`robots.py:94-95`).
- Pydantic `field_validator` on `NetworkConfig.insecure_tls` (`config.py:218-223`) rejects `True`.
- SSL context is always `ssl.create_default_context()` (`robots.py:187`).

### 6.3 Input Validation

- Pydantic models with `extra = "forbid"` prevent unexpected fields.
- Zod schemas on all MCP tool inputs with min/max length constraints.
- `resolveConfiguredSource` (`source_resolver.ts:20-56`) rejects direct URLs in `ensure_docs`, requiring a named source alias.

### 6.4 XML Safety

- `defusedxml.ElementTree` for all sitemap XML parsing (prevents XXE, billion laughs).
- Sitemap size limit: 50 MB (`sitemap.py:41`).
- Sitemap nesting depth limit: 5 (`sitemap.py:42`).

### 6.5 SQL Injection Prevention

- All PostgreSQL queries use parameterized statements (`$1`, `$2`, etc.).
- All SQLite queries use parameterized statements (`?` placeholders).
- No string concatenation in query construction.

### 6.6 Path Traversal Prevention

- `SaveStep._validate_output_path()` (`save.py:44-65`) resolves paths and checks `relative_to(base_dir)`.
- `_url_to_filename()` (`fetcher.py:36-71`) sanitizes URL paths with `re.sub(r"[^\w\-]", "_", filename)`.

### 6.7 Resource Limits

- Content size: 50 MB per response (`client.py:102`).
- Download time: 300s max (`client.py:103`).
- Redirect limit: 10 hops (`client.py:104`).
- robots.txt redirect limit: 5 hops (`robots.py:78`).
- Connection pool: 100 total, 10 per host (`client.py:210-211`).
- Rate limiting: per-host with adaptive backoff (`rate_limiter.py`).

### 6.8 Secrets Handling

- `.gitignore` covers `.env`, `.venv`, IDE files, cache directories.
- MCP `.gitignore` covers `.env` and `node_modules/`.
- Auth credentials support env var expansion (`$VAR` / `${VAR}`) to avoid CLI history exposure (`config.py:149-168`).
- No hardcoded secrets found in codebase.
- `.env.example` uses placeholder values.

---

## 7. Auto-Fixes Applied

| Fix | Description | Status |
|---|---|---|
| `.gitignore` hardening | Added `.env.*`, `!.env.example`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.crt` patterns | **Applied** |
| Dependency lower bounds | Added explicit safe lower bounds for vulnerable transitive packages surfaced by `pip-audit` | **Applied** |
| MCP regex search hardening | Added per-line regex timeout for `grep_docs` to prevent single-line catastrophic backtracking | **Applied** |
| MCP source config validation | User-edited source aliases now reject unsafe names, non-HTTPS URLs, private/local literal hosts, and invalid page limits | **Applied** |
| MCP embedding writes | Library re-indexing now replaces derived rows atomically inside one transaction instead of deleting first and inserting later | **Applied** |
| MCP OpenAI hardening | Embedding calls now use bounded timeouts, bounded retries, dimension checks, and a small circuit breaker | **Applied** |
| CI / supply-chain gates | Added secret scanning, dependency review, CodeQL, release gates, and PR-based metrics updates | **Applied** |

No other hygiene fixes were applicable:
- No web server to add security headers middleware to (CLI tool + static site + stdio MCP)
- No session cookies to harden
- YAML already uses `safe_load`
- XML already uses `defusedxml`
- TLS verification already mandatory and can't be disabled
- No committed `.env` files or secrets found
- Dependencies use standard Python `>=` minimum-version pinning (acceptable for a library)

---

## 8. Reviewed Risks / Current Status

### RESOLVED

1. **Dangerous integration hook fields removed**: `IntegrationConfig`, `post_process_hook`, `git_commit`, and `git_message` are no longer exposed on `DocpullConfig`; regression tests assert this stays removed.

2. **`grepDocs` ILIKE wildcard DoS mitigated**: The TypeScript MCP database search escapes `%`, `_`, and `\` before building the parameterized `ILIKE` pattern.

3. **Header injection blocked**: User-Agent and custom auth header values reject CR, LF, and null bytes at config and transport layers.

4. **Manual source-config bypass blocked**: Python and MCP source loaders now validate hand-edited `sources.yaml` entries instead of trusting direct URL values.

5. **Partial MCP re-index writes blocked**: Refreshing one library's embeddings now deletes and reinserts inside the same transaction, with rollback tests.

6. **Dependency audit gates clean locally**: `pip-audit`, `npm audit`, and `bun audit` are expected to be clean before release.

### REMAINING BY DESIGN

1. **Proxy mode delegates DNS pinning to the proxy**: Direct connections use `_ValidatedResolver` for connect-time DNS pinning. Proxy mode cannot enforce local connect-time DNS pinning because the proxy resolves the target. Agent workflows that require the stronger posture should set `--require-pinned-dns`, which rejects `--proxy`.

2. **Markdown output may contain prompt-injection content**: docpull intentionally preserves documentation content. Downstream LLM clients must treat fetched content as untrusted context.

3. **Auth env-var expansion assumes trusted local config**: `$VAR` / `${VAR}` expansion is useful for local secret hygiene but means config files must not be loaded from untrusted sources.

4. **Cache integrity depends on local filesystem trust**: If an attacker can write to the cache directory, they can tamper with cached metadata. Keep cache directories user-owned and non-shared.

---

## 9. Summary Assessment

The docpull codebase demonstrates **above-average security posture** for a documentation fetching tool:

- SSRF defenses are comprehensive and layered (validation + connect-time resolver pinning + redirect re-validation)
- TLS enforcement is mandatory with no escape hatch
- XML parsing uses defusedxml
- All SQL is parameterized
- Path traversal is checked
- Input validation uses Pydantic (strict) and Zod
- The MCP server properly rejects direct URL input, requiring named source aliases

The primary residual risks are operational: proxy mode delegates DNS resolution to the configured proxy, fetched Markdown is untrusted LLM context, and local cache/config files must be treated as trusted user-controlled inputs.

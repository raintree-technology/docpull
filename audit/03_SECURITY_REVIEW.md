# Security Review

## Threat Model

DocPull accepts URLs from users and AI agents, fetches remote content, parses HTML/XML/JSON/YAML-like metadata, follows links and redirects, writes local files/cache manifests, and exposes local cached docs through MCP. It may run in developer environments containing cloud credentials, source code, SSH keys, API tokens, and private network access.

Primary attacker capabilities:
- Provide malicious URL, hostname, DNS behavior, redirects, HTML, robots.txt, sitemap XML, OpenAPI JSON, or metadata.
- Influence cached validator headers by controlling prior server responses.
- Hand-edit MCP `sources.yaml` or convince an agent to add malicious sources.
- Plant symlinks or strange paths in output/cache dirs if local filesystem boundary is weak.
- Trigger expensive regexes through MCP search.

## Confirmed Mitigations

- HTTPS-only default: `UrlValidator.DEFAULT_ALLOWED_SCHEMES = {"https"}` in `src/docpull/security/url_validator.py:48-50`.
- Local/private/internal host blocking: `url_validator.py:50-57`, `211-237`.
- DNS root-dot stripping: `url_validator.py:113-121`.
- DNS rebinding TOCTOU mitigation: single resolution returned from `resolve_allowed_addresses()` in `url_validator.py:159-197`; aiohttp resolver dials those addresses in `http/client.py:32-76`.
- Redirect target validation: `http/client.py:203-267`.
- Sensitive auth header stripping cross-origin: `http/client.py:209-235`.
- robots.txt pinned HTTPS and fail-closed behavior: `security/robots.py:47-76`, `151-203`.
- robots.txt 512 KB body cap: `security/robots.py:97-99`.
- XXE-resistant sitemap parsing: `discovery/sitemap.py:9-10`, `149-166`.
- YAML frontmatter injection mitigation: `conversion/markdown.py:218-277`.
- Conditional request header sanitization: `pipeline/steps/fetch.py` contains `_sanitize_validator_header` per grep evidence.
- MCP library/path traversal controls: `mcp/sources.py:65-72`, `mcp/tools.py:647-665`.
- MCP ReDoS controls: `mcp/tools.py:45-49`, `485-500`, `537-541`.

## Findings

### SEC-001: Current worktree import failure disables security/runtime verification

- Severity: High
- Confidence: High
- Surface: CLI, Python API, Python MCP, tests
- Evidence: `src/docpull/models/events.py:131-140` annotates `FetchEvent.progress(...)-> FetchEvent` without `from __future__ import annotations`; `docpull --version`, `--help`, `--doctor`, `pytest`, and coverage fail with `NameError: name 'FetchEvent' is not defined`.
- Reproduction: `./.venv/bin/docpull --version`.
- Expected: version output.
- Actual: import traceback before argument parsing.
- Recommended fix: add postponed annotations or quote return type; add CLI import smoke tests.
- Tests to add: `test_import_docpull_package`, `test_cli_version_no_network`, `test_mcp_subcommand_help_no_import_failure`.

### SEC-002: Proxy mode weakens DNS pinning unless user opts into `--require-pinned-dns`

- Severity: Medium
- Confidence: High
- Surface: CLI/network
- Evidence: `AsyncHttpClient.__aenter__` logs warning and disables `_ValidatedResolver` when `_proxy is not None` in `src/docpull/http/client.py:269-279`; `require_pinned_dns` rejects proxy mode in `src/docpull/http/client.py:162-168`.
- Reproduction: configure `--proxy` without `--require-pinned-dns`.
- Expected: security model remains clear and enforceable for agent use.
- Actual: DNS resolution is delegated to proxy after preflight URL validation.
- Current mitigation: warning plus `--require-pinned-dns`.
- Gap: agents/users may miss warning; docs mention it but default remains weaker.
- Recommended fix: for MCP/agent paths, default to requiring pinned DNS or require explicit `allow_proxy_dns=true`.
- Tests: CLI config test for `--proxy --require-pinned-dns`; MCP/Fetcher test ensuring proxy warning and rejection behavior.

### SEC-003: Sitemap size limit is applied after full response materialization

- Severity: Medium
- Confidence: Medium
- Surface: sitemap fetch
- Evidence: `_fetch_sitemap()` calls `response = await self._client.get(url)` then checks `len(response.content) > MAX_SITEMAP_SIZE` in `src/docpull/discovery/sitemap.py:132-143`.
- Reproduction: malicious sitemap endpoint streams >50 MB but HTTP client max is higher/default 50 MB.
- Expected: streaming cap at sitemap limit.
- Actual: response may be read into memory up to generic client cap before sitemap-specific rejection.
- Current mitigation: generic HTTP max content size and sitemap post-check.
- Gap: sitemap-specific cap is not enforced during read.
- Recommended fix: add per-request max body override to HTTP client and use 512 KB or 50 MB intentionally; document exact cap.
- Tests: local server streaming oversized sitemap and assert early abort below memory budget.

### SEC-004: Plugin docs direct users to wrong cache path, which can cause stale/private data confusion

- Severity: Low
- Confidence: High
- Surface: Claude plugin user docs
- Evidence: `plugin/README.md:63-65` says `$XDG_DATA_HOME/docpull/docs`; `default_docs_dir()` returns `$XDG_DATA_HOME/docpull-mcp/docs` or `~/.local/share/docpull-mcp/docs` in `src/docpull/mcp/sources.py:114-120`.
- Reproduction: run `list_indexed()` after fetch and inspect default directory.
- Expected: docs path matches implementation.
- Actual: plugin docs point to a different directory.
- Recommended fix: update plugin README and troubleshooting.
- Tests: documentation lint or unit test asserting README contains `docpull-mcp/docs`.

### SEC-005: MCP user source registry has no cross-process lock

- Severity: Low
- Confidence: High
- Surface: `add_source` / `remove_source`
- Evidence: `_write_user_sources()` notes "Last writer wins - no cross-process lock" in `src/docpull/mcp/tools.py:721-728`.
- Reproduction: concurrent MCP/server/manual writes to `sources.yaml`.
- Expected: no lost updates.
- Actual: possible lost update.
- Current mitigation: atomic replace prevents partial file.
- Recommended fix: file lock around read-modify-write or document single-writer assumption.
- Tests: concurrent add/remove simulation with lock.

### SEC-006: `read_doc` large-file guard prevents full reads but allows slice request only after size rejection

- Severity: Info
- Confidence: Medium
- Surface: MCP `read_doc`
- Evidence: `read_doc` rejects any file >1 MB before reading even if line slice is requested in `src/docpull/mcp/tools.py:666-671`.
- Risk: availability/usability rather than confidentiality; users cannot inspect large files via safe slice.
- Recommended fix: when `line_start`/`line_end` is present, stream line-by-line up to requested range with byte budget.

## v4.0.0 Security Claim Verification

- DNS-rebinding TOCTOU fix: Verified statically in `UrlValidator.resolve_allowed_addresses()` and `_ValidatedResolver`.
- Wider SSRF coverage: Verified statically for CGNAT, IPv4-mapped IPv6, root-dot hostnames; TS MCP additionally blocks `nip.io`, `sslip.io`, `xip.io`.
- robots.txt body cap: Verified in `RobotsChecker.MAX_ROBOTS_SIZE`.
- YAML frontmatter injection fix: Verified in `FrontmatterBuilder._inline()` and list item quoting.
- Conditional request header sanitization: Present by grep in `FetchStep`; runtime unverified due import failure.
- Release/dependency hardening: Verified workflows and `requirements-release.txt` presence; `pip-audit` could not run due DNS.


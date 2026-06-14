# Security Review

## Threat Model

DocPull accepts URLs from users and AI agents, fetches remote content, parses
HTML/XML/JSON/metadata, follows links and redirects, writes local files/cache
manifests, and exposes local cached docs through MCP. It may run in developer
environments containing cloud credentials, source code, SSH keys, API tokens,
and private network access.

Primary attacker capabilities:

- Provide malicious URL, hostname, DNS behavior, redirects, HTML, robots.txt,
  sitemap XML, OpenAPI JSON, or metadata.
- Influence cached validator headers by controlling prior server responses.
- Hand-edit MCP `sources.yaml` or convince an agent to add malicious sources.
- Plant symlinks or strange paths in output/cache dirs if local filesystem
  boundaries are weak.
- Trigger expensive regexes through MCP search.

## Confirmed Mitigations

- HTTPS-only default URL policy.
- Local/private/internal host blocking, including CGNAT and IPv4-mapped IPv6.
- DNS root-dot stripping.
- DNS-rebinding TOCTOU mitigation via single screened resolution and
  connect-time address pinning when not using a proxy.
- Redirect target validation.
- Sensitive auth header stripping across origins.
- robots.txt handling and body cap.
- XXE-resistant sitemap parsing via `defusedxml`.
- YAML frontmatter injection mitigation.
- Conditional request header sanitization.
- MCP library/path traversal controls.
- MCP regex length and timeout controls.
- Output path validation for save steps.

## Findings

### SEC-001: Proxy mode delegates DNS pinning unless user opts into `--require-pinned-dns`

- Severity: Medium
- Confidence: High
- Surface: CLI/network
- Current mitigation: warning plus `--require-pinned-dns`.
- Risk: agents/users may miss the warning and assume the same DNS-pinning
  guarantee applies through the proxy.
- Recommended fix: for MCP/agent paths, require explicit opt-in to delegated
  proxy DNS or default to pinned DNS.
- Tests: CLI config test for `--proxy --require-pinned-dns`; Fetcher/MCP test
  ensuring proxy rejection behavior.

### SEC-002: Sitemap size limit is applied after generic HTTP read

- Severity: Medium
- Confidence: Medium
- Surface: sitemap fetch
- Current mitigation: generic HTTP response-size caps plus sitemap post-check.
- Risk: a sitemap-specific cap may not abort as early as intended.
- Recommended fix: add per-request body limit override to the HTTP client and
  use it for sitemaps/robots with tests against streaming oversized responses.

### SEC-003: MCP user source registry has no cross-process lock

- Severity: Low
- Confidence: High
- Surface: `add_source` / `remove_source`
- Current mitigation: atomic replace prevents partial writes.
- Risk: concurrent writers can lose updates.
- Recommended fix: file lock around read-modify-write or document single-writer
  assumption.

### SEC-004: Authenticated/internal docs mode is not a complete product model

- Severity: Medium
- Confidence: High
- Surface: CLI/API auth options
- Current mitigation: scoped auth stripping on cross-origin redirects and header
  injection checks.
- Risk: productizing private docs without allowlists, redaction, and audit logs
  could leak sensitive content or credentials through outputs/logs.
- Recommended fix: design explicit private-docs mode before promoting it:
  allowlisted domains, scoped secrets, redaction, audit logs, privacy mode, and
  strong output warnings.

## Security Claim Verification

- URL validation and DNS pinning: verified by code and security tests.
- Redirect revalidation/auth stripping: verified by tests.
- robots/sitemap XML handling: verified by tests for robots behavior and XXE
  sitemap rejection.
- Frontmatter injection defenses: verified by conversion tests.
- SQLite FTS: local-only output feature; search helper does not execute SQL
  constructed from string interpolation.
- Scraper API: thin wrapper around Fetcher, so it inherits the same URL/network
  policy instead of adding a new fetch path.
- Plugin cache path docs: checked against `default_docs_dir()` in CI policy
  tests.

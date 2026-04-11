# Security Policy

## Security Features

- **HTTPS-only fetching**: docpull rejects non-HTTPS targets and re-validates every request URL, including redirect targets.
- **SSRF controls with connect-time pinning**: private, loopback, link-local, and cloud metadata addresses are blocked, and hostname resolution is pinned at connect time to prevent DNS rebinding.
- **Scoped authenticated crawling**: sitemap discovery stays on the crawl origin, and sensitive headers are stripped from off-origin requests and cross-origin redirects.
- **robots.txt enforcement**: robots.txt is fetched through the same validated transport path, and fetch or parser failures fail closed.
- **Path traversal protection**: output paths are validated and generated filenames are sanitized.
- **XXE protection**: sitemap XML is parsed with `defusedxml`.
- **Download guardrails**: response size limits, timeout controls, and retry backoff are enforced in the HTTP client.

## Reporting Vulnerabilities

Report security issues to **support@raintree.technology**.

Include: description, reproduction steps, potential impact.

Do not open public GitHub issues for security vulnerabilities.

## Supply Chain Security

- Python dependencies in `pyproject.toml` declare supported minimum versions; JavaScript dependency trees are pinned in `web/package-lock.json` and `mcp/bun.lock`.
- Automated scanning runs in `.github/workflows/security.yml` and executes `pip-audit`, `bandit`, `bun audit`, and `npm audit`.
- Targeted security regression tests run alongside those audits to catch SSRF, credential-scoping, robots, and indexing regressions before merge.

# Compliance Posture

This page states how docpull identifies itself, which access-control signals
it honors, and where it stops. Each claim maps to code in this repository;
the load-bearing ones are pinned by tests.

## Identity

Every request sends the User-Agent:

```
docpull/<version> (+https://github.com/raintree-technology/docpull)
```

docpull never spoofs a browser identity. The default User-Agent contains no
`Mozilla` token, and `tests/test_security_hardening.py`
(`test_default_user_agent_is_honest_docpull_identity`) fails the build if
that changes. Site operators can identify, rate-limit, or block docpull by
this string.

## robots.txt

- Mandatory. There is no flag to disable robots.txt checking.
- RFC 9309 semantics: a 4xx response means "no policy" (allow); a 5xx
  response or a fetch error blocks the host (fail closed).
- robots.txt bodies are capped at 512 KB.
- The robots.txt fetch itself goes over HTTPS through the same DNS-pinned
  connection path as page fetches, so it cannot be redirected to an
  unvalidated address.
- `Crawl-delay` is read per host and applied to the per-host rate limiter;
  the effective delay is the larger of the configured rate limit and the
  site's declared delay.

Implementation: `src/docpull/security/robots.py`,
`src/docpull/core/fetcher.py` (`_apply_robots_crawl_delay`).

## Rate limiting

- Per-host defaults: 0.5 seconds minimum between requests, at most 3
  concurrent requests per host.
- With `--adaptive-rate-limit`, a 429 response (or `Retry-After` header)
  raises the per-host delay by a factor of 2 up to 60 seconds; sustained
  success lowers it back down to a floor of 0.1 seconds. `Retry-After`
  values are honored directly, capped at 60 seconds.

Implementation: `src/docpull/http/rate_limiter.py`.

## AI/TDM opt-out signals

docpull honors machine-readable opt-outs at the page level, by default:

- `X-Robots-Tag` response headers and HTML `<meta name="robots">` tags
  carrying `noai` or `noimageai` cause the page to be skipped and nothing
  is written to disk. Directives scoped to `docpull` (for example
  `X-Robots-Tag: docpull: noai` or `<meta name="docpull" ...>`) also apply;
  directives scoped to other agents do not.
- `noindex` and `none` govern search indexing, not reuse, so they do not
  block by default. `--respect-noindex` turns on the stricter mode that
  treats them as opt-outs too.
- `--no-respect-ai-optout` disables opt-out enforcement. It exists for
  mirroring your own content or sources whose owners explicitly authorized
  reuse. It does not affect robots.txt, which cannot be disabled.

Implementation: `src/docpull/security/optout.py`,
`src/docpull/pipeline/steps/fetch.py` (headers),
`src/docpull/pipeline/steps/convert.py` (meta tags).

## Scope boundaries

- HTTPS-only fetching by default, with URL validation, private-network
  blocking, DNS pinning at connect time, and redirect revalidation (SSRF
  protections).
- No CAPTCHA solving or bot-defense evasion.
- No stealth: no header camouflage, no browser fingerprint imitation.
- No circumvention of authentication walls. Auth headers are only sent when
  the user configures them, and they are stripped on cross-origin redirects.
- Remote documents (PDFs) are fetched and parsed only when the user passes
  `--remote-documents pdf` explicitly.

The full statement of what docpull will and will not fetch is in
[Web Source Boundary](scraping-boundary.md).

## What docpull does not do

- No proxy rotation. A single user-supplied `--proxy` is supported; docpull
  never cycles addresses to evade blocks.
- No fingerprint spoofing of any kind.
- No paid provider or cloud calls without budget consent: paid-capable
  routes require an explicit `--budget`, and `--budget 0` forbids them
  entirely.

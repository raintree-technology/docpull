# Crawl, frontier and politeness: verified flow

Scope: discovery, frontier persistence and resume, robots, rate limits, redirects and
download limits. Models: `tests/test_model_crawl_politeness.py`. Regression tests against the
real code: `tests/test_crawl_regressions.py`.

Legend: green is **Fixed** in this change, red is **Bug found** and still open, plain is
**Works** (checked by the model and by existing tests).

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Discovered: sitemap or link crawl
    Discovered --> Filtered: URL, robots, scope, pattern, depth, page limit
    Discovered --> SitemapLoop: sitemap lists itself
    SitemapLoop --> Filtered: each sitemap fetched once
    Filtered --> StartFetched: start URL disallowed
    StartFetched --> Filtered: start URL skipped
    Filtered --> Queued: written to frontier
    Queued --> InFlight: worker takes it
    InFlight --> RateWait: per-host limit and delay
    RateWait --> RateWait: host spelled with other case
    RateWait --> InFlight: one host key, Crawl-delay kept
    InFlight --> Redirected: 3xx response
    Redirected --> InFlight: SSRF re-checked, max 10 hops
    Redirected --> RobotsBlocked: target path disallowed
    InFlight --> Retrying: 429 or 5xx
    Retrying --> InFlight: backoff, bounded attempts
    InFlight --> Done: saved
    InFlight --> Failed: error after retries
    InFlight --> Skipped: robots, opt-out, duplicate
    Done --> [*]
    Skipped --> [*]
    Failed --> Resumed: crash or rerun
    InFlight --> Resumed: crash
    Queued --> Resumed: crash
    Resumed --> Queued: only unfinished URLs
    Done --> Queued: first snapshot wiped it
    Queued --> Done: snapshot keeps finished pages

    class StartFetched,SitemapLoop,RateWait,RobotsBlocked fixed
    classDef bug stroke:#c33,color:#c33
    classDef fixed stroke:#393,color:#393
```

## Findings

| State | Status | Detail |
| --- | --- | --- |
| Filtered, then Queued | Works | page and depth limits, dedup of normalized URLs, scope and SSRF checks |
| StartFetched | Fixed | `LinkCrawler.discover` fetched a robots-disallowed or invalid start URL for its links |
| SitemapLoop | Fixed | a sitemap index referencing itself or siblings was refetched up to depth 5 (exponential) |
| RateWait | Fixed | host keys were case sensitive, so `Docs.Example.com` and `docs.example.com` got separate concurrency slots, delays and Crawl-delay config |
| RateWait (adaptive) | Fixed | `AdaptiveRateLimiter` overwrote a robots Crawl-delay after 10 successes or a short `Retry-After` |
| Resumed, Done to Queued | Fixed | the first streaming snapshot reset pages finished before it back to queued, so resume refetched them |
| Redirected | Works | every hop is SSRF-validated, download-policy checked and capped at 10 |
| RobotsBlocked | Fixed | redirect targets were never re-checked against robots.txt; the fetcher now passes its robots check to `AsyncHttpClient.redirect_allowed` |
| Retrying | Works | attempts bounded by `max_retries + 1`, each with up to 10 redirects |

## Model invariants

| Model | States | Invariants |
| --- | --- | --- |
| Frontier, workers, crash, resume | 10,881 | no URL succeeds twice; page limit; depth limit; attempts at most 1 + crashes; completed crawl leaves nothing queued or in flight; every URL that reached disk is covered |
| Redirect chain | 29 | private target never requested; robots-denied target never requested (caught on the pre-fix model); hops bounded |
| Limiter | 105 | per-host concurrency; spacing at least the Crawl-delay |
| Sitemap walk | 11 | each sitemap fetched at most once |

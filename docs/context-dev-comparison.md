# Context.dev Comparison

Context.dev packages web scraping, brand intelligence, styleguide extraction,
product extraction, structured extraction, screenshots, and web search behind a
hosted API. DocPull now covers the local, auditable part of that workflow with
typed context packs.

## What DocPull Owns Locally

- Static and server-rendered web to Markdown, NDJSON, SQLite, and OKF.
- Typed local packs for brand, styleguide, products, schema extraction, images,
  screenshots, and local search.
- `source_policy.json`, citations, replay config, and non-secret accounting
  artifacts.
- Explicit rendering through the existing trusted-target renderer gate.
- Explicit provider-backed search-pack dry runs and budget blocking.

## What Remains Provider Or Hosted

- Managed proxy/browser infrastructure, geo behavior, and bundled provider
  billing.
- Global web search and proprietary web indexes.
- Broad firmographics such as employee counts, revenue, valuation, and industry
  classification unless locally cited or returned by an explicit provider.
- Transaction descriptor enrichment and hosted logo CDN delivery.
- Always-on schedules, webhook receivers, org retention, SSO, audit dashboards,
  and SLAs.

## Product Rule

Provider-backed calls must be opt-in, budgeted, and auditable. DocPull should
record provider, request options, estimated or returned cost, cache/rate-limit
metadata, warnings, provenance, and artifacts, but it should not present provider
proxy behavior as a local OSS capability.

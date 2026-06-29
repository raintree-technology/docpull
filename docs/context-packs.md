# Context Packs

DocPull context packs turn a public site or existing local pack into typed,
file-backed context. They stay local-first: direct async HTTP extraction is the
default, rendering is explicit, and provider-backed search is budgeted.

## Commands

```bash
docpull brand-pack example.com -o packs/brand
docpull styleguide-pack https://example.com -o packs/styleguide
docpull product-pack https://example.com/pricing --mode page -o packs/products
docpull extract-schema packs/products --schema schema.json -o packs/schema
docpull image-pack https://example.com -o packs/images
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull screenshot-pack https://example.com -o packs/screenshot
docpull search-pack "pricing" --provider local --pack-dir packs/products -o packs/search
docpull search-pack "current docs" --provider parallel --dry-run --budget 0
```

## Artifacts

Each workflow writes a result JSON, Markdown report, `source_policy.json`,
citations or basis records where relevant, replay config, and pack metadata.
Each pack also writes `run.accounting.json`; local workflows record zero paid
cost with HTTP/cache counts, while paid-capable provider routes include budget
and blocked-action metadata.

- `brand-pack`: `brand.result.json`, `BRAND.md`, `brand.assets.json`
- `styleguide-pack`: `styleguide.result.json`, `STYLEGUIDE.md`, `tokens.json`, `tokens.css`
- `product-pack`: `products.result.json`, `PRODUCTS.md`, `products.ndjson`, `pricing.matrix.json`
- `extract-schema`: `structured.result.json`, `STRUCTURED.md`, `basis.ndjson`
- `image-pack`: `image.result.json`, `VISUALS.md`, `images.ndjson`, `image.assets.json`
- `screenshot-pack`: `screenshot.result.json`, `SCREENSHOT.md`, `screenshots/*.png`
- `search-pack`: `search.result.json`, `SEARCH.md`, `search.results.ndjson`

## Boundaries

Context packs do not infer hidden firmographics, solve CAPTCHA challenges, use
stealth scraping, or silently call paid providers. Unknown product prices stay
`null`; broad search requires an explicit provider and budget.

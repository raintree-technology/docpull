# DocPull v5.5: Typed Local Context Packs

DocPull v5.5 adds local-first context packs for brand, design, product,
structured extraction, visual assets, screenshots, and search.

The release keeps the core DocPull boundary: async HTTP extraction stays the
default, browser rendering is explicit, provider-backed search is budgeted, and
every workflow writes durable local artifacts with provenance.

```bash
pip install docpull

docpull brand-pack example.com -o packs/brand
docpull styleguide-pack https://example.com -o packs/styleguide
docpull product-pack https://example.com/pricing -o packs/products
docpull extract-schema packs/products --schema schema.json -o packs/schema
docpull image-pack https://example.com -o packs/images
docpull search-pack "pricing" --provider local --pack-dir packs/products -o packs/search
```

New pack families include:

- `brand-pack` for evidence-backed brand profiles, social links, logo/icon
  candidates, colors, contacts, and cited local metadata.
- `styleguide-pack` for CSS variables, colors, font stacks, component samples,
  spacing, radii, and shadows without downloading remote fonts by default.
- `product-pack` for local Product/Offer JSON-LD, pricing rows, and cited
  product records.
- `extract-schema` for deterministic JSON Schema-shaped extraction from a URL
  or existing local pack.
- `image-pack` and `screenshot-pack` for visual evidence, with screenshot
  capture behind `DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1`.
- `search-pack` for local pack search plus explicit provider dry-runs and
  budget-gated provider routes.

Every pack writes a result JSON, Markdown report, `source_policy.json`,
citations or basis records, replay config, pack metadata, and
`run.accounting.json`.

The useful claim is still narrow:

> DocPull builds auditable, file-backed context from public sources before it
> ever asks for a browser or a paid provider.

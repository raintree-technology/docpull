# Website snapshots

`website-pack` is DocPull's canonical acquisition workflow for downstream
competitive-intelligence systems. It writes a portable-v3 artifact whose entrypoint is
`website.snapshot.v1.json` and whose complete file set is pinned by
`artifact.manifest.json`.

```bash
docpull website-pack https://example.com -o packs/example-website
docpull website-pack https://example.com -o packs/example-next \
  --baseline-pack packs/example-website
```

The default bounded crawl is 50 pages and depth 3. It classifies home, product,
pricing, documentation, trust, legal, changelog, support, and other pages. Every
active page has a stable canonical-URL `document_id`, a content-hash
`document_version`, source authority, entity reference, page role, and OKF
representation. Raw HTML is on by default. At most eight key-page screenshots
are included when `DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1` and a compatible
local browser runtime are available; missing browser capability produces a
typed warning and does not invalidate text evidence.

## Baseline states

A baseline must pass recursive manifest, reference, pack/run identity, schema,
and snapshot-hash verification before use. The new snapshot explicitly reports
`added`, `changed`, `unchanged`, `removed`, `failed`, and `blocked` document
states. Unchanged documents remain in `documents.ndjson`; removed documents
remain in the snapshot manifest without pretending they were acquired again.

`validate_website_snapshot_pack(path)` verifies the current manifest and every
referenced representation. Files outside the manifest are ignored, so stale
files from an older output directory cannot alter current-run identity.

## Artifact layout

- `website.snapshot.v1.json` — canonical snapshot identity, baseline, options,
  documents, coverage, and representation references.
- `documents.ndjson` — the active evidence corpus used by downstream analysis.
- `okf/` — one OKF concept per active document.
- `raw/` — selected source HTML when raw capture is enabled.
- `screenshots/` and `brand-assets/` — bounded optional visual evidence.
- `corpus.manifest.json`, `current-run.manifest.json`,
  `coverage.manifest.json`, and `provenance.manifest.json` — portable-v3
  inventory, run, coverage, and provenance layers.
- `artifact.manifest.json` — sorted relative paths, byte counts, media types,
  and SHA-256 hashes for the exact current artifact.

The bundled schema is `website-snapshot.v1.schema.json`. Its file SHA-256 is
written into every snapshot as `schema_sha256`, allowing consumers to pin the
exact producer contract.

## Local replay

`brand-pack`, `product-pack`, `policy-pack`, `styleguide-pack`, `image-pack`,
and `relationship-pack` accept an existing DocPull pack path. Local-pack mode
does not fetch the network or download referenced assets. This makes one
website acquisition reusable across deterministic extraction lanes.

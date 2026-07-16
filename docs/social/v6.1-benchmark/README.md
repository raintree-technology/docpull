# DocPull 6.1 benchmark social images

LinkedIn-ready 4:5 carousel images for the DocPull 6.1.0 benchmark post.

Recommended carousel order:

1. `01-cover.png`
2. `02-core-results.png`
3. `03-boundaries.png`
4. `04-pdf-isolation.png`
5. `05-integrity.png`
6. `06-evidence.png`

The `screenshots/` directory contains direct captures of the public PyPI,
GitHub release, benchmark comparison, and evidence-status pages. These work
best as supporting images after the designed carousel or in a follow-up post.

For a single-image post, use `assets/02-core-results.png`. For the full story,
publish all six numbered cards in order. Keep `03-boundaries.png` in any
carousel that includes the core score so the acquisition boundary is visible.

Additional existing product images that can accompany a follow-up post:

- `../../launch-assets/screenshot-hero-desktop-1280x720.png`
- `../../launch-assets/docpull-project-diff-demo.png`
- `../../launch-assets/docpull-routing-table-v5.png`
- `../../launch-assets/docpull-evidence-formats-table-v5.png`
- `../v6-launch/assets/contact-sheet.png`

Regenerate the cards from the repository root:

```bash
uv run --with pillow python docs/social/v6.1-benchmark/generate_cards.py
```

The benchmark cards reproduce the stored development result. They must remain
paired with its limitations: the v5 bundle is public development evidence, is
not claim-grade, and does not establish comparative superiority for the
released 6.1.0 wheel.

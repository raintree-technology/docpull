# Archived v5.5 Release Note

The v5.5 typed-pack command surface has been superseded by the v3
consolidation. New release material should describe the core path:

```bash
docpull https://docs.example.com -o packs/example
docpull pack prepare packs/example --eval-grade
docpull pack validate packs/example --level eval
docpull export packs/example --format openai-vector-jsonl --output exports/example.jsonl
docpull ci packs/example --prepare
```

The current release story is not scraper parity or standalone pack-builder
aliases. It is current, cited, auditable context dependencies for agents.

# DocPull Release Examples

These examples cover the supported release surface: context dependencies,
v3 pack contracts, validation/preparation, exports, Context CI, document
parsing, OpenAPI packs, explicit rendering, and monitors.

## Project Context

```bash
docpull init stripe-docs
docpull add stripe react postgres
docpull install
docpull sync
docpull deps
docpull diff
docpull review
docpull export context-pack --target codex
```

## Pack Contract

```bash
docpull https://docs.example.com -o packs/docs
docpull pack validate packs/docs --level raw
docpull pack prepare packs/docs --eval-grade
docpull pack validate packs/docs --level eval
docpull export packs/docs --format openai-vector-jsonl -o exports/openai.jsonl
```

## Local Inputs

```bash
docpull parse ./handbook.pdf -o packs/handbook --backend auto
docpull openapi-pack ./openapi.json -o packs/api
docpull feed-pack ./feed.xml -o packs/feed
docpull paper-pack ./paper.md arxiv:1706.03762 -o packs/papers
docpull repo-pack psf/requests -o packs/repo
docpull package-pack pypi:requests -o packs/package
docpull standards-pack rfc:9110 -o packs/standard
docpull dataset-pack ./metrics.csv -o packs/dataset
docpull transcript-pack ./meeting.vtt -o packs/transcript
```

## Context CI

```bash
docpull ci --prepare
```

Minimal GitHub Actions step:

```yaml
- run: pip install docpull
- run: docpull ci --prepare
```

## Rendering

Rendering is off by default. Explicit rendering uses the canonical
`agent-browser --json` contract locally and inside supported cloud sandboxes.

```bash
docpull render https://example.com/app --runtime local --check
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 \
  docpull render https://example.com/app --runtime local -o rendered
```

Cloud rendering remains explicit:

```bash
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 \
  docpull render https://example.com/app --runtime e2b --budget 1
```

## Release Smoke

Run the real-data free/local surface smoke before cutting a release candidate:

```bash
python scripts/release_a_plus_check.py --strict
python scripts/real_feature_smoke.py --json --full-mcp --strict-ci --auth-matrix --monitor-soak-minutes 10
```

`release_a_plus_check.py --strict` requires synchronized generated metadata and
a clean `git status --short`; run it from the exact tree you intend to tag.

Cloud/keyed lanes stay explicit:

```bash
python scripts/real_feature_smoke.py --include-cloud --json
```

## Monitors

```bash
docpull monitor init
docpull monitor list
docpull monitor run
docpull monitor report
```

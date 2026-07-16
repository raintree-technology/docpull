# Context Packs

DocPull context packs are file-backed context dependencies for agents. The
public release path is deliberately small: fetch or parse sources, prepare a v3
pack, validate the contract, then export or gate it in CI.

## Core Path

```bash
docpull https://docs.example.com --max-pages 25 -o packs/example
docpull parse ./docs --output-dir packs/local-docs
docpull openapi-pack ./openapi.json --output-dir packs/api
docpull feed-pack https://example.com/news --output-dir packs/news
docpull paper-pack arxiv:1706.03762 --output-dir packs/papers
docpull repo-pack psf/requests --output-dir packs/repo
docpull package-pack pypi:requests --output-dir packs/package
docpull standards-pack rfc:9110 --output-dir packs/standard
docpull dataset-pack ./metrics.csv --output-dir packs/dataset
docpull transcript-pack ./meeting.vtt --output-dir packs/transcript
docpull wiki-pack wiki:Web_scraping --output-dir packs/wiki
docpull brand-pack example.com --output-dir packs/brand
docpull product-pack https://example.com/pricing --output-dir packs/product
docpull styleguide-pack example.com --output-dir packs/styleguide
docpull image-pack example.com --output-dir packs/visuals
docpull policy-pack example.com --output-dir packs/policies

docpull pack prepare packs/example --eval-grade
docpull pack validate packs/example --level eval
docpull export packs/example --format openai-vector-jsonl --output exports/example.jsonl
docpull export packs/example --format cursor-rules --output .cursor/rules --skill-name example
docpull ci packs/example --prepare
```

Browser-free HTTP extraction is the default. Explicit rendering uses
`agent-browser` through one of the supported runtimes:

```bash
docpull render https://example.com --runtime local --output-dir rendered/example
docpull https://example.com --render fallback --render-runtime e2b
```

## Contract Levels

| Level | Use | Required sidecars |
| --- | --- | --- |
| `raw` | Loadable extraction output | `corpus.manifest.json`, `sources.md`, `acquisition.routes.json` |
| `agent` | Agent-ready local context | raw sidecars plus `context.lock.json`, `coverage.report.json`, `citation.index.json`, `pack.score.json`, `pack.audit.json` |
| `eval` | CI/eval-grade dependency | agent sidecars plus `rights.manifest.json`, `provenance.graph.json`, basis/eval artifacts, `PACK_CARD.md` |

Use the validator as the source of truth:

```bash
docpull pack validate packs/example --level raw
docpull pack validate packs/example --level agent --format json
docpull pack validate packs/example --level eval
```

## Typed Knowledge Lanes

Typed lanes are narrow pack builders for high-value agent context dependencies.
They are not discovery, search, or browser-automation commands; each lane turns
known sources into the same v3 raw contract and can then be prepared, validated,
exported, or checked in Context CI.

| Lane | Inputs | Default output |
| --- | --- | --- |
| `paper-pack` | local papers, `arxiv:<id>`, `doi:<doi>`, `pmid:<id>`, HTTPS metadata URLs | paper metadata, abstracts/content, arXiv PDF text when explicitly requested, references |
| `repo-pack` | public GitHub URL or `owner/repo[@ref]` | repo metadata, selected docs/manifests, releases |
| `package-pack` | `npm:<name>` or `pypi:<name>` | registry metadata, README/description, versions, dependencies |
| `standards-pack` | `rfc:<n>`, `ietf:<draft>`, `w3c:<shortname>`, `whatwg:<url>` | standard metadata, section-level records, references |
| `dataset-pack` | local CSV, TSV, JSON, NDJSON, SQLite, optional Parquet; HTTPS JSON/CSV | schema, exact streamable row counts, bounded data dictionary records, and remote snapshot provenance |
| `transcript-pack` | local VTT, SRT, text, JSON, or direct transcript URL | timestamped transcript segment records |
| `wiki-pack` | `wiki:<title>`, `wikipedia:<title>`, or Wikimedia/MediaWiki page URLs | page metadata, license/revision metadata, lead and section-level records from the MediaWiki REST API |

Remote typed lanes support `--cache --cache-dir .docpull-cache/typed-packs`
for repeatable official API/metadata calls. Python SDK users can call the
matching `async_build_*_pack` helpers when already inside an event loop.

## Evidence Workflow Protocol

Brand, product, styleguide, visual/image, screenshot, policy, relationship, and dataset lanes implement
the common workflow protocol. Their existing result JSON and Markdown remain,
and every run additionally writes:

- `workflow.request.json` with stable input, policy, budget, and replay fields;
- `workflow.result.json` with progress, warnings, failures, budget usage,
  identities, and hashes;
- `artifact.manifest.json` with byte sizes and SHA-256 for emitted artifacts.

Use `docpull pack intelligence-bundle PACK` to create the deterministic tracker
import. Use `docpull pack diff OLD NEW` to write `change.events.jsonl` alongside
the existing diff artifacts.

## Release Smoke

The full free/local public surface can be checked against real public data with
the opt-in smoke harness:

```bash
python scripts/real_feature_smoke.py --json
```

Use `--include-cloud` only in environments configured with the relevant cloud
tools or keys. Cloud failures are reported separately from the required
free/local lanes.

## Boundaries

DocPull does not silently call paid services, bypass site controls, solve
CAPTCHAs, or promote exploratory workflows as release commands. External
browser automation, hosted research, search providers, and private workflows
belong outside the public pack contract unless their outputs are normalized
back into a v3 pack and validated.

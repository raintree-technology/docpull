# Context Pack Contract v3

DocPull output contract v3 separates local context artifacts into three levels.

## Levels

| Level | Required artifacts | Use |
| --- | --- | --- |
| Raw | `corpus.manifest.json`, `sources.md`, `acquisition.routes.json` | Inspect or export freshly extracted output |
| Agent | Raw plus `context.lock.json`, `coverage.report.json`, `citation.index.json`, `pack.score.json`, `pack.audit.json` | Load into agents with local quality checks |
| Eval | Agent plus `rights.manifest.json`, `provenance.graph.json`, `basis.ndjson`, `basis.report.json`, `PACK_CARD.md` | CI-gated or eval-generating context |

Validate a pack with:

```bash
docpull pack validate ./packs/example --level raw
docpull pack validate ./packs/example --level agent --format json
docpull pack validate ./packs/example --level eval
```

## Record Shape

Newly written records use `schema_version: 3` and include stable document or
chunk IDs, source URL, title, content hash, fetch/render timestamps when known,
content type, MIME type, token count, route metadata, and conservative rights
state. Legacy v1/v2 packs remain readable, but v3 validation fails them until
they are regenerated.

## Citations

Source citations remain stable as `S1`, `S2`, and so on. v3 also exposes
record-level citations such as `S1.1` for a specific document, chunk, or listing
item parent record. Exports preserve both fields as `citation_id` and
`record_citation_id`.

## Agent Preparation

`docpull pack prepare` writes the agent-level sidecars. With `--eval-grade`, it
also writes rights, provenance, citation index, basis, and pack-card artifacts.
For link-dense listing pages, preparation may also write `listing.items.ndjson`
so agents can reason over individual event/news cards while keeping the original
page as the source of record.

## Local Documents

`docpull parse` converts local files into the same raw v3 pack shape. Plain
text, Markdown, JSON, CSV, and HTML files are parsed directly. Complex formats
such as PDF, DOCX, PPTX, and XLSX use optional open-source parser backends:

```bash
pip install 'docpull[parse]'
docpull parse ./handbook.pdf -o ./packs/handbook --backend auto
docpull pack validate ./packs/handbook --level raw
```

The parse lane writes `documents.ndjson`, source Markdown files under
`sources/`, `corpus.manifest.json`, `sources.md`, and
`acquisition.routes.json`. Add `--prepare` or `--eval-grade` to immediately
write the agent/eval sidecars after parsing.

## Typed Knowledge Lanes

`website-pack` is the canonical bounded website lane. In addition to the v3
corpus, it writes `website.snapshot.v1.json`, explicit baseline states, OKF and
optional visual representations, and recursive artifact hashes. See
[Website snapshots](website-snapshot.md).

Known-source lanes use the same v3 raw contract: `openapi-pack`,
`feed-pack`, `paper-pack`, `repo-pack`, `package-pack`, `standards-pack`,
`dataset-pack`, `transcript-pack`, and `wiki-pack` all write
`documents.ndjson`, source Markdown files, raw sidecars, lane `.pack.json`,
lane `.index.json`, lane `.items.ndjson`, and a short lane summary Markdown
file. Each lane supports `--prepare` and `--eval-grade`; the validator remains
the source of truth for raw, agent, and eval readiness.

Typed lane sidecars use stable typed roots. Standards packs include
section-level records for precise citations, dataset packs report exact row
counts for streamable table formats, and remote metadata lanes can use
`--cache` for repeat runs without changing the v3 contract.

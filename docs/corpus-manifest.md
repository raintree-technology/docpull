# Corpus Manifest

Every docpull output sink writes `corpus.manifest.json` next to generated
artifacts. The manifest is the stable source map for a crawl: it lets agents,
RAG jobs, pack diff tools, and audits connect records back to URLs and files
without reparsing every output format.

## Top-level fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Manifest schema version. Current value: `1`. |
| `generated_at` | UTC timestamp when the manifest was written. |
| `output_format` | Output sink that produced the records: `markdown`, `json`, `ndjson`, `sqlite`, or `okf`. |
| `run` | Stable, non-secret run identity derived from profile, crawl, output, extractor, and JS policy settings. |
| `document_count` | Unique logical document count. |
| `record_count` | Total emitted records. For chunked output this may exceed `document_count`. |
| `chunk_count` | Number of records with `chunk_id`. |
| `records` | Ordered record source map. |

## Record fields

Each `records[]` entry may include:

| Field | Meaning |
| --- | --- |
| `document_id` | Stable ID derived from URL and content hash. |
| `url` | Original source URL. |
| `title` | Extracted title when available. |
| `source_type` | Detected/extracted source type such as `next_data`, `openapi`, `docusaurus`, or `sphinx`. |
| `content_hash` | SHA-256 hash of the emitted content field/body. For Markdown and OKF, this includes frontmatter added by that sink. For JSON, NDJSON, and SQLite, record metadata fields are not part of this hash. |
| `output_path` | Path to the generated artifact relative to the output directory, or `"-"` for NDJSON records streamed to stdout. |
| `chunk_id` | Stable chunk ID derived from URL, chunk index, heading, and chunk content hash. |
| `chunk_index` | Zero-based chunk position within the source page. |
| `chunk_heading` | Heading context captured by the chunker. |
| `token_count` | Token count or estimate for chunked records. |

## Stability contract

- `document_id` is stable for identical URL and emitted content.
- `document_id` changes when emitted content changes.
- `chunk_id` is stable for identical URL, chunk index, chunk heading, and emitted
  chunk content.
- `content_hash` describes the emitted content body, including frontmatter when
  the sink adds it. It is not a hash of the surrounding JSON/SQLite record
  envelope.
- File-backed `output_path` values are relative so packs can move between
  machines. NDJSON stdout records use `"-"`.
- The manifest does not include secrets or auth headers.

Consumers should treat unknown fields as forward-compatible metadata and should
gate strict validation on `schema_version`.

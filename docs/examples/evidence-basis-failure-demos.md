# Evidence Basis Failure Demos

These two local demos show the product claim behind Context CI: DocPull fails
or warns when an agent answer is stale, uncited, or not supported by current
source evidence. They use only local files and local-only DocPull commands.

## Docs/API Failure

Create a minimal API context pack, prepare trust artifacts, and grade an agent
answer that cites removed behavior:

```bash
mkdir -p /tmp/docpull-basis-docs/sources
cat > /tmp/docpull-basis-docs/documents.ndjson <<'JSONL'
{"document_id":"doc_api","url":"https://docs.example.test/api","title":"Example API","content":"Example API returns current cited JSON results. Deprecated legacy behavior is no longer supported.","content_hash":"hash_api","source_type":"demo"}
JSONL
cat > /tmp/docpull-basis-docs/corpus.manifest.json <<'JSON'
{"schema_version":1,"document_count":1,"record_count":1,"records":[{"document_id":"doc_api","url":"https://docs.example.test/api","content_hash":"hash_api"}]}
JSON
printf '%s\n' 'Example API returns current cited JSON results.' > /tmp/docpull-basis-docs/sources/01.md
docpull pack prepare /tmp/docpull-basis-docs --eval-grade --no-search --no-graph
docpull pack basis /tmp/docpull-basis-docs --claim "legacy behavior is supported"
docpull ci /tmp/docpull-basis-docs --strict
```

Expected signal: `basis_quality` fails because the claim is unsupported by the
current pack.

## Product/Pricing Failure

Create a non-doc product/pricing pack and ask DocPull to prove an outdated price
claim:

```bash
mkdir -p /tmp/docpull-basis-pricing/sources
cat > /tmp/docpull-basis-pricing/documents.ndjson <<'JSONL'
{"document_id":"doc_price","url":"https://example.test/pricing","title":"Example Pricing","content":"Example Pro costs $29 per seat per month. The retired Starter plan is no longer offered.","content_hash":"hash_price","source_type":"demo"}
JSONL
cat > /tmp/docpull-basis-pricing/corpus.manifest.json <<'JSON'
{"schema_version":1,"document_count":1,"record_count":1,"records":[{"document_id":"doc_price","url":"https://example.test/pricing","content_hash":"hash_price"}]}
JSON
printf '%s\n' 'Example Pro costs $29 per seat per month.' > /tmp/docpull-basis-pricing/sources/01.md
docpull pack prepare /tmp/docpull-basis-pricing --eval-grade --no-search --no-graph
docpull pack basis /tmp/docpull-basis-pricing --claim "Example Starter costs $9 per month"
docpull ci /tmp/docpull-basis-pricing --strict
```

Expected signal: `basis_quality` fails because the non-doc pricing claim has no
supported current evidence. This is the same CI contract used for docs/API
context.

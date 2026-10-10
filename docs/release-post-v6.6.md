# DocPull 6.6: frozen installation and locked-context queries

DocPull 6.6 restores exact locked context on a fresh machine and retrieves cited
passages across locked packs without fetching sources.

## Restore locked context

Each new sync saves a SHA-256-addressed archive and pins its digest in the
context lockfile. Transfer the project manifest, lockfile, and archive to another
machine, then restore the captured files and rebuild the local index:

```bash
docpull install --frozen --artifact-dir /path/to/transferred-artifacts
```

Installation validates the lock and archive before changing project state.
Missing or tampered archives, unsafe archive paths, source drift, and conflicting
local runs fail explicitly. Frozen installation preserves the lockfile and
captured evidence; it never substitutes newly fetched content.

## Query across locked packs

```bash
docpull query "retry policy" --project ./payments --project ./client --max-tokens 8000
```

The CLI, Python SDK `query_context`, and MCP `context_query` share one retrieval
implementation. Queries support exact document-version and locked-run filters,
deduplicate identical passages, and return citations with source URLs, content
hashes, archive identities, and exact character and line spans.

The response budget counts UTF-8 bytes as a conservative token bound, including
citations and metadata. It is not a model-specific token count. Paragraphs and
fenced code blocks are returned whole or omitted. Retrieval uses local lexical
ranking and verifies the selected archives on every call; it does not require
embeddings or download tokenizer data.

## Upgrade and compatibility

```bash
pip install --upgrade 'docpull==6.6.0'
# For the Python MCP server:
pip install --upgrade 'docpull[mcp]==6.6.0'
# For the TypeScript pack-reading and CLI SDK:
npm install @raintree-technology/docpull-sdk@6.6.0
```

The Python package, npm SDK, plugin metadata, and MCP registry metadata use
version 6.6.0. The new query API is exposed through the Python SDK, CLI, and
Python MCP server. The TypeScript SDK retains its pack-reading and CLI APIs.

Existing locks remain valid for ordinary installation. Frozen installation and
locked queries require a pinned archive; create one with a new sync or export an
older retained run. A lockfile alone cannot recover content that was not saved.
Archives include retained source content, so transfer them only to authorized
recipients.

This release also updates vulnerable dependencies. Core Python support remains
3.10 and newer. The patched optional Unstructured backend requires Python
3.11–3.13; use pypdf, MarkItDown, or text on other interpreters where the input
format permits. The optional Crawl4AI benchmark baseline was removed because
its NLTK dependency has an unpatched advisory. Readability and Trafilatura remain
available as benchmark baselines.

See the [context dependency guide](https://github.com/raintree-technology/docpull/blob/v6.6.0/docs/context-dependencies.md)
for transfer steps, query filters, budget semantics, and citation fields, and the
[changelog](https://github.com/raintree-technology/docpull/blob/v6.6.0/docs/CHANGELOG.md)
for the complete release changes.

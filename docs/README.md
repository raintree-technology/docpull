# docpull Documentation

This directory contains maintained docs for the current `docpull` package.
The root [README](../README.md) is the canonical quick-start and feature
overview; files here provide focused setup notes and copy-pasteable examples.

## Current Version

The docs in this directory are aligned with docpull 4.0.0:

- Python 3.10+
- CLI entry point: `docpull`
- MCP server entry point: `docpull mcp`
- Supported output formats: `markdown`, `json`, `ndjson`, `sqlite`
- Supported profiles: `rag`, `mirror`, `quick`, `llm`
- Config files use the `DocpullConfig` shape from `src/docpull/models/config.py`

## Files

| Path | Purpose |
|---|---|
| [examples/](examples/) | Valid YAML config examples and equivalent CLI commands |
| [mcp-pgvector-setup.md](mcp-pgvector-setup.md) | Optional TypeScript MCP server with PostgreSQL + pgvector semantic search |
| [CHANGELOG.md](CHANGELOG.md) | Historical release notes |

## Configuration Shape

docpull 4.x accepts one target URL per `DocpullConfig`. For multiple sites,
run the CLI once per URL, create one config per source, or use the MCP
`add_source` / `ensure_docs` alias workflow.

Valid YAML mirrors the Python model:

```yaml
profile: rag
url: https://docs.example.com
crawl:
  max_pages: 200
  max_depth: 3
output:
  directory: ./docs/example
  format: markdown
content_filter:
  streaming_dedup: true
cache:
  enabled: true
```

Removed 2.x fields such as `language`, `deduplicate`, `exclude_sections`,
`create_index`, `incremental`, `update_only_changed`, and top-level
`sources:` are intentionally not used in current examples. Pydantic forbids
unknown fields so stale configs fail loudly instead of being ignored.

## MCP Choices

Most users should run the Python stdio server that ships with the package:

```bash
pip install 'docpull[mcp]'
docpull mcp
```

The root-level `mcp/` directory is a separate TypeScript server for users who
specifically need PostgreSQL, pgvector, and OpenAI embeddings for semantic
search. See [mcp-pgvector-setup.md](mcp-pgvector-setup.md).

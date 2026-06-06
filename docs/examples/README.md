# Configuration Examples

These examples use the current `DocpullConfig` YAML shape. They are meant for
Python callers, tests, and agent workflows that construct `DocpullConfig` from
YAML:

```python
from pathlib import Path
from docpull import DocpullConfig

config = DocpullConfig.from_yaml(Path("docs/examples/simple-optimization.yaml").read_text())
```

The CLI does not currently accept a config-file flag. Each example also includes
an equivalent `docpull ...` command in comments. Files that start with a YAML
list contain one valid `DocpullConfig` payload per list item.

## Files

| File | Description |
|------|-------------|
| `simple-optimization.yaml` | RAG-oriented Markdown crawl with rich metadata and streaming dedup |
| `multi-source-optimized.yaml` | Sequential single-source configs for a multi-site docs refresh |
| `incremental-updates.yaml` | Cache + resume configuration for changed-page refreshes |
| `format-conversion.yaml` | JSON, NDJSON, and SQLite output examples |
| `deduplication-strategies.yaml` | Current streaming dedup behavior and alternatives |
| `selective-crawling.yaml` | Include/exclude path patterns for scoped crawls |

## Current Field Reference

```yaml
profile: rag
url: https://docs.example.com
crawl:
  max_pages: 200
  max_depth: 3
  max_concurrent: 20
  rate_limit: 0.5
  include_paths: ["*/guides/*"]
  exclude_paths: ["*/changelog"]
content_filter:
  streaming_dedup: true
  max_file_size: 200kb
  extractor: default
  enable_special_cases: true
  strict_js_required: false
output:
  directory: ./docs/example
  format: markdown
  naming_strategy: full
  rich_metadata: true
cache:
  enabled: true
  directory: .docpull-cache
  ttl_days: 30
```

Removed fields from older examples are intentionally absent: top-level
`sources:`, `language`, `deduplicate`, `keep_variant`, `exclude_sections`,
`create_index`, `incremental`, and `update_only_changed` are not accepted by
docpull 4.x.

For the optional TypeScript MCP server backed by PostgreSQL, pgvector, and
OpenAI embeddings, see [pgvector MCP Setup](../mcp-pgvector-setup.md).

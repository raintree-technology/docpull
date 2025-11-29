# Configuration Examples

Example YAML configurations for docpull.

## Files

| File | Description |
|------|-------------|
| `simple-optimization.yaml` | Single source with language filter + index |
| `multi-source-optimized.yaml` | Multiple sources with full optimization |
| `incremental-updates.yaml` | Resume downloads, update only changed files |
| `format-conversion.yaml` | TOON, JSON, SQLite output formats |
| `deduplication-strategies.yaml` | Different dedup strategies (mainnet, shortest, etc.) |
| `selective-crawling.yaml` | Include/exclude path patterns |

## Usage

```bash
docpull --sources-file docs/examples/simple-optimization.yaml
```

## Configuration Reference

```yaml
# Global settings
output_dir: ./docs
rate_limit: 0.5
git_commit: true
git_message: "Update docs - {date}"
archive: true
archive_format: tar.gz

# Per-source settings
sources:
  my-docs:
    url: https://example.com
    language: en
    deduplicate: true
    keep_variant: mainnet
    max_file_size: 200kb
    include_paths: ["guides/*"]
    exclude_paths: ["*/changelog"]
    exclude_sections: ["Examples"]
    format: markdown
    create_index: true
```

See `docpull --help` for all options.

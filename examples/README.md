# Docpull Configuration Examples

This directory contains example configuration files demonstrating various features of docpull v1.2.0.

## Quick Start Examples

### Simple Optimization
**File**: `simple-optimization.yaml`

Basic optimization for a single documentation source with language filtering and index generation.

```bash
docpull --sources-file examples/simple-optimization.yaml
```

**Features demonstrated**:
- Language filtering (English only)
- Size limits (max 200KB per file)
- Auto-index generation
- Metadata extraction

---

### Multi-Source Optimized (Recommended)
**File**: `multi-source-optimized.yaml`

Real-world configuration that reduces 31 MB → 13 MB (58% reduction) across 4 documentation sources.

```bash
docpull --sources-file examples/multi-source-optimized.yaml
```

**Features demonstrated**:
- Multi-source configuration
- Language filtering (skips 352+ translation files)
- Deduplication (removes 304 duplicates)
- Size limits
- Selective crawling (include/exclude paths)
- Content filtering (remove sections)
- Git integration (auto-commit)
- Archive creation
- Metadata extraction

**Result**: Optimized, navigable documentation with automatic version control.

---

### Incremental Updates
**File**: `incremental-updates.yaml`

Only download changed files, resume interrupted downloads.

```bash
docpull --sources-file examples/incremental-updates.yaml
```

**Features demonstrated**:
- Incremental mode (resume capability)
- Update detection (only download changed files)
- Cache management
- Git integration (auto-commit changes)

**Use case**: Regular documentation updates with minimal bandwidth usage.

---

## Format Conversion Examples

### Format Conversion
**File**: `format-conversion.yaml`

Convert documentation to different output formats for various use cases.

```bash
docpull --sources-file examples/format-conversion.yaml
```

**Formats**:
- **TOON**: 40-60% size reduction, optimized for LLMs
- **JSON**: Structured JSON with sections and metadata
- **SQLite**: Searchable database with FTS5 full-text search

**Use cases**:
- TOON: LLM training data, compact storage
- JSON: API integration, structured analysis
- SQLite: Full-text search, documentation portals

---

## Advanced Examples

### Deduplication Strategies
**File**: `deduplication-strategies.yaml`

Different strategies for handling duplicate files.

```bash
docpull --sources-file examples/deduplication-strategies.yaml
```

**Strategies**:
- **mainnet**: Keep files matching "mainnet" pattern (skip testnet/devnet)
- **shortest**: Keep shortest variant
- **first**: Keep first file encountered
- **last**: Keep last file encountered
- **longest**: Keep longest variant

**Use case**: Aptos Move reference docs exist for 3 environments (mainnet/testnet/devnet) - deduplication removes 2/3 of files.

---

### Selective Crawling
**File**: `selective-crawling.yaml`

Only fetch specific sections of documentation.

```bash
docpull --sources-file examples/selective-crawling.yaml
```

**Features demonstrated**:
- Include patterns (only guides and tutorials)
- Exclude patterns (skip changelog, release notes)
- Per-source configuration

**Use case**: Only need guides, not full API reference or historical changelog.

---

## Command-Line Usage

All features can also be used from the command line:

```bash
# Simple optimization
docpull https://code.claude.com/docs --language en --create-index

# Advanced optimization
docpull https://aptos.dev \
  --deduplicate \
  --keep-variant mainnet \
  --max-file-size 200kb \
  --include-paths "build/*" \
  --exclude-paths "*/changelog" \
  --create-index

# Format conversion
docpull https://docs.anthropic.com --format toon --language en

# Incremental updates
docpull https://docs.anthropic.com \
  --incremental \
  --update-only-changed \
  --git-commit \
  --git-message "Update docs - {date}"
```

---

## Configuration File Reference

### Global Settings

```yaml
output_dir: ./docs          # Output directory
rate_limit: 0.5            # Seconds between requests
log_level: INFO            # Logging level (DEBUG, INFO, WARNING, ERROR)
skip_existing: true        # Skip existing files
dry_run: false             # Dry run mode (don't download)
```

### Source-Specific Settings

```yaml
sources:
  source-name:
    url: https://example.com

    # Language filtering
    language: en                      # Include only this language
    exclude_languages: [fr, de, ja]   # Exclude these languages

    # Deduplication
    deduplicate: true                 # Remove duplicates
    keep_variant: mainnet             # Keep files matching this pattern

    # Size limits
    max_file_size: 200kb              # Max file size (kb, mb, gb)
    max_total_size: 500mb             # Max total download size

    # Selective crawling
    include_paths:                    # Only crawl these paths
      - "guides/*"
      - "api/*"
    exclude_paths:                    # Skip these paths
      - "*/changelog"
      - "*/release-notes"

    # Content filtering
    exclude_sections:                 # Remove these sections
      - "Examples"
      - "Changelog"
      - "Full Response"

    # Output format
    format: markdown                  # markdown, toon, json, sqlite
    naming_strategy: full             # full, short, flat, hierarchical

    # Index generation
    create_index: true                # Generate INDEX.md

    # Metadata
    extract_metadata: true            # Extract to metadata.json

    # Update detection
    update_only_changed: true         # Only download changed files
    incremental: true                 # Resume capability
    cache_dir: .docpull-cache         # Cache directory

    # Per-source output directory
    output_dir: ./custom-output       # Override global output_dir
```

### Git Integration

```yaml
git_commit: true                      # Auto-commit changes
git_message: "Update docs - {date}"   # Commit message template
```

Templates: `{date}`, `{datetime}`, `{timestamp}`

### Archive Mode

```yaml
archive: true                         # Create archive
archive_format: tar.gz                # tar.gz, tar.bz2, tar.xz, zip
```

---

## Real-World Impact

Testing with 1,914 files (31 MB) from Anthropic, Claude Code, Aptos, and Shelby documentation:

| Optimization | Files Removed | Size Saved |
|--------------|---------------|------------|
| Language filtering (en only) | 352 files | 5-10 MB |
| Deduplication (mainnet only) | 304 files | 10 MB |
| Size limits (200kb max) | 8 files | 3-5 MB |
| Content filtering | - | 1-2 MB |
| **Total** | **664 files** | **19-27 MB (58% reduction)** |

**Result**: 31 MB → 13 MB with all indexes generated and full navigation.

---

## Tips

1. **Start simple**: Use `simple-optimization.yaml` as a base
2. **Test with dry run**: Add `dry_run: true` to test configuration
3. **Check logs**: Use `log_level: DEBUG` for detailed output
4. **Use incremental mode**: Save bandwidth on regular updates
5. **Combine optimizations**: Stack language filtering + deduplication + size limits for maximum impact
6. **Version control**: Enable `git_commit` to track documentation changes over time
7. **Archive for distribution**: Use `archive: true` to create single-file bundles

---

## Need Help?

- **Documentation**: See main README.md
- **Troubleshooting**: See TROUBLESHOOTING.md
- **Bug reports**: https://github.com/raintree-technology/docpull/issues
- **Feature requests**: https://github.com/raintree-technology/docpull/issues

---

**Version**: 1.2.0
**Date**: 2025-11-16

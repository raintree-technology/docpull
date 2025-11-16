# docpull

**Pull documentation from any website and converts it into clean, AI-ready Markdown.**
Fast, type-safe, secure, and optimized for building knowledge bases or training datasets.

**NEW in v1.2.0**: 15 major features including language filtering, deduplication, auto-indexing, multi-source configuration, and more. Real-world testing shows **58% size reduction** with automatic optimization.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/docpull.svg)](https://badge.fury.io/py/docpull)
[![License: MIT](https://img.shields.io/github/license/raintree-technology/docpull)](https://github.com/raintree-technology/docpull/blob/main/LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy-blue.svg)](http://mypy-lang.org/)
[![Security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)

## Why docpull?

Unlike tools like wget or httrack, docpull extracts only the main content, removing ads, navbars, and clutter. Output is clean Markdown with optional YAML frontmatter—ideal for RAG systems, offline docs, or ML pipelines.

## Key Features

### Core Features (v1.0+)
- Works on any documentation site
- Smart extraction of main content
- Async + parallel fetching (up to 10× faster)
- Optional JavaScript rendering via Playwright
- Sitemap + link crawling
- Rate limiting, timeouts, content-type checks
- Saves docs in structured Markdown with YAML metadata
- Optimized profiles for popular platforms (Stripe, Next.js, React, Plaid, Tailwind, etc.)

### NEW in v1.2.0: Advanced Optimization
- **Language Filtering**: Auto-detect and filter by language (skip 352+ translation files)
- **Deduplication**: Remove duplicates with SHA-256 hashing (save 10+ MB on duplicate content)
- **Auto-Index Generation**: Create navigable INDEX.md with tree/TOC/categories/stats
- **Size Limits**: Control file and total download size (skip/truncate oversized files)
- **Multi-Source Configuration**: Configure multiple docs in one YAML file
- **Selective Crawling**: Include/exclude URL patterns for targeted fetching
- **Content Filtering**: Remove verbose sections (Examples, Changelog, etc.)
- **Format Conversion**: Output to Markdown, TOON (compact), JSON, or SQLite
- **Smart Naming**: 4 naming strategies (full, short, flat, hierarchical)
- **Metadata Extraction**: Extract titles, URLs, stats to metadata.json
- **Update Detection**: Only download changed files (checksums, ETags)
- **Incremental Mode**: Resume interrupted downloads with checkpointing
- **Hooks & Plugins**: Python plugin system for custom processing
- **Git Integration**: Auto-commit changes with customizable messages
- **Archive Mode**: Create tar.gz/zip archives for distribution

**Real-world impact**: Testing with 1,914 files (31 MB) → **13 MB (58% reduction)** with all optimizations enabled.

## Quick Start

```bash
pip install docpull
docpull --doctor         # verify installation

# Basic usage
docpull https://aptos.dev
docpull stripe           # use a built-in profile

# NEW: Simple optimization (v1.2.0)
docpull https://code.claude.com/docs --language en --create-index

# NEW: Advanced optimization (v1.2.0)
docpull https://aptos.dev \
  --deduplicate \
  --keep-variant mainnet \
  --max-file-size 200kb \
  --create-index

# NEW: Multi-source configuration (v1.2.0)
docpull --sources-file examples/multi-source-optimized.yaml
```

### JavaScript-heavy sites

```bash
pip install docpull[js]
python -m playwright install chromium
docpull https://site.com --js
```

## Python API

```python
from docpull import GenericAsyncFetcher

fetcher = GenericAsyncFetcher(
    url_or_profile="https://aptos.dev",
    output_dir="./docs",
    max_pages=100,
    max_concurrent=20,
)
fetcher.fetch()
```

## Common Options

### Core Options
- `--doctor` – verify installation and dependencies
- `--max-pages N` – limit crawl size
- `--max-depth N` – restrict link depth
- `--max-concurrent N` – control parallel fetches
- `--js` – enable Playwright rendering
- `--output-dir DIR` – output directory
- `--rate-limit X` – seconds between requests
- `--no-skip-existing` – re-download existing files
- `--dry-run` – test without downloading

### NEW in v1.2.0: Optimization Options
- `--language LANG` – filter by language (e.g., `en`)
- `--exclude-languages LANG [LANG ...]` – exclude languages
- `--deduplicate` – remove duplicate files
- `--keep-variant PATTERN` – keep files matching pattern when deduplicating
- `--max-file-size SIZE` – max file size (e.g., `200kb`, `1mb`)
- `--max-total-size SIZE` – max total download size
- `--include-paths PATTERN [PATTERN ...]` – only crawl matching URLs
- `--exclude-paths PATTERN [PATTERN ...]` – skip matching URLs
- `--exclude-sections NAME [NAME ...]` – remove sections by header name
- `--format {markdown,toon,json,sqlite}` – output format
- `--naming-strategy {full,short,flat,hierarchical}` – file naming strategy
- `--create-index` – generate INDEX.md with navigation
- `--extract-metadata` – extract metadata to metadata.json
- `--update-only-changed` – only download changed files
- `--incremental` – enable incremental mode with resume
- `--git-commit` – auto-commit changes
- `--git-message MSG` – commit message template
- `--archive` – create compressed archive
- `--archive-format {tar.gz,tar.bz2,tar.xz,zip}` – archive format
- `--sources-file PATH` – multi-source configuration file

See `docpull --help` for complete list of options.

## Performance

Async fetching drastically reduces runtime:

| Pages | Sync | Async | Speedup |
|-------|------|-------|---------|
| 50 | ~50s | ~6s | 8× faster |

Higher concurrency yields even better results.

## Output Format

Each downloaded page becomes a Markdown file:

```markdown
---
url: https://stripe.com/docs/payments
fetched: 2025-11-13
---
# Payment Intents
...
```

Directory layout mirrors the target site's structure.

## Configuration File

### Simple Configuration (v1.0+)

```yaml
output_dir: ./docs
rate_limit: 0.5
sources:
  - stripe
  - nextjs
```

Run with:
```bash
docpull --config config.yaml
```

### NEW: Multi-Source Configuration (v1.2.0)

```yaml
sources:
  anthropic:
    url: https://docs.anthropic.com
    language: en
    max_file_size: 200kb
    create_index: true

  claude-code:
    url: https://code.claude.com/docs
    language: en  # Skips 352 translation files!
    create_index: true

  aptos:
    url: https://aptos.dev
    deduplicate: true
    keep_variant: mainnet  # Skips 304 duplicates!
    max_file_size: 200kb
    include_paths:
      - "build/guides/*"

output_dir: ./docs
rate_limit: 0.5
git_commit: true
git_message: "Update docs - {date}"
extract_metadata: true
archive: true
```

Run with:
```bash
docpull --sources-file config.yaml
```

See `examples/` directory for more configuration examples.

## Custom Profiles

Easily define profiles for frequently scraped sites.

```python
from docpull.profiles.base import SiteProfile

MY_PROFILE = SiteProfile(
    name="mysite",
    domains={"docs.mysite.com"},
    include_patterns=["/docs/", "/api/"],
)
```

## Security

- HTTPS-only
- Blocks private network IPs
- 50MB page size limit
- Timeout controls
- Validates content-type
- Playwright sandboxing

## Troubleshooting

- **Installation issues**: Run `docpull --doctor` to diagnose problems
- **Missing dependencies**: See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common fixes
- **Site requires JS**: install Playwright + `--js`
- **Slow or rate limited**: lower concurrency or raise `--rate-limit`
- **Large sites**: set `--max-pages`

For detailed troubleshooting, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## v1.2.0 Feature Examples

### Language Filtering
Automatically detect and filter documentation by language:
```bash
# English only (auto-detects /en/, _en_, docs_en_, etc.)
docpull https://code.claude.com/docs --language en --create-index
```
**Impact**: Claude Code docs downloaded in 9 languages = 352 unnecessary files for English-only users.

### Deduplication
Remove duplicate files based on content hash:
```bash
# Keep mainnet version, skip testnet/devnet duplicates
docpull https://aptos.dev --deduplicate --keep-variant mainnet --create-index
```
**Impact**: Aptos Move reference docs across 3 environments = 304 duplicate files (~10 MB saved).

### Format Conversion
Convert to different formats for various use cases:
```bash
# TOON format (40-60% size reduction, optimized for LLMs)
docpull https://docs.anthropic.com --format toon --language en

# SQLite database with full-text search
docpull https://docs.anthropic.com --format sqlite --language en

# Structured JSON
docpull https://docs.anthropic.com --format json --language en
```

### Incremental Updates
Only download changed files:
```bash
docpull https://docs.anthropic.com \
  --incremental \
  --update-only-changed \
  --git-commit \
  --git-message "Update docs - {date}"
```
**Use case**: Regular documentation updates with minimal bandwidth usage.

### Complete Optimization Pipeline
Combine all optimizations:
```bash
docpull --sources-file examples/multi-source-optimized.yaml
```
See `examples/` directory for comprehensive configuration examples.

**Real-world results**: Testing with 4 documentation sources (Anthropic, Claude Code, Aptos, Shelby):
- **Before**: 1,914 files, 31 MB, no navigation
- **After**: 1,250 files, 13 MB (58% reduction), full indexes generated
- **One command** instead of 4+ separate commands with manual optimization

## What's New in v1.2.0

This release adds 15 major features across 4 phases. See [CHANGELOG.md](CHANGELOG.md) for complete release notes.

**Highlights**:
- Multi-source YAML configuration
- Language filtering with auto-detection
- SHA-256 based deduplication
- Auto-index generation (tree, TOC, categories, stats)
- 4 output formats (Markdown, TOON, JSON, SQLite)
- Incremental mode with resume capability
- Git integration and archive creation
- Python plugin/hook system

**Backward Compatible**: All v1.0+ workflows continue to work unchanged.

## Links

- [PyPI](https://pypi.org/project/docpull/)
- [GitHub](https://github.com/raintree-technology/docpull)
- [Issues](https://github.com/raintree-technology/docpull/issues)
- [Changelog](https://github.com/raintree-technology/docpull/blob/main/CHANGELOG.md)
- [Examples](https://github.com/raintree-technology/docpull/tree/main/examples)

## License

MIT License - see [LICENSE](LICENSE) file for details

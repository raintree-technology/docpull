# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2025-11-16

### Added - 15 Major New Features

This release represents a massive expansion of docpull's capabilities, adding 15 major features across 4 phases. Based on real-world usage pulling 1,914 files (31 MB) from Anthropic, Claude Code, Aptos, and Shelby documentation, these features enable automatic optimization reducing output to ~13 MB (58% reduction).

**Note**: All new features are backward compatible. Existing workflows continue to work unchanged.

#### Phase 1: Essential Optimizations (Top Priority Features)

**1. Language Filtering** (`--language`, `--exclude-languages`)
- Filter documentation by language code during download or post-process
- Automatic detection from URL patterns (`/en/`, `_en_`, `docs_en_`, etc.)
- **Real-world impact**: Claude Code docs downloaded in 9 languages = 352 unnecessary files for English-only users
- Example: `docpull https://code.claude.com/docs --language en`

**2. Deduplication** (`--deduplicate`, `--keep-variant`, `--remove-patterns`)
- Remove duplicate files based on SHA-256 content hash
- Keep specific variants (e.g., `mainnet` vs `testnet/devnet`)
- Configurable keep strategies: first, last, shortest, longest, pattern
- **Real-world impact**: Aptos docs had 456 Move reference files across 3 environments (2/3 duplicates = 304 files, ~10 MB saved)
- Example: `docpull https://aptos.dev --deduplicate --keep-variant mainnet`

**3. Auto-Index Generation** (`--create-index`, `--index-styles`, `--per-directory-index`)
- Generate INDEX.md with file tree, table of contents, categories, and statistics
- Per-directory indexes for nested documentation
- **Real-world impact**: Makes 1,914 files actually navigable and usable
- Index styles: tree, toc (table of contents), categories, stats
- Example: `docpull https://docs.anthropic.com --create-index`

**4. Size Limits** (`--max-file-size`, `--max-total-size`, `--size-limit-action`)
- Skip, truncate, or warn on oversized files
- Prevent runaway downloads with total size limits
- **Real-world impact**: Some REST API docs were 308 KB with full JSON responses
- Actions: skip (default), truncate (keep first N bytes), warn (log only)
- Example: `docpull https://aptos.dev --max-file-size 200kb --max-total-size 500mb`

**5. Multi-Source Configuration** (`--sources-file`, `--generate-sources-config`)
- Configure multiple documentation sources in a single YAML file
- Per-source settings for language, deduplication, size limits, etc.
- **Real-world impact**: One command instead of 4+ separate commands with manual optimization
- Repeatable, version-controlled documentation workflows
- Example: `docpull --sources-file my-docs.yaml`

#### Phase 2: Content Control

**6. Selective Crawling** (`--include-paths`, `--exclude-paths`)
- Only download URLs matching include patterns
- Skip entire branches matching exclude patterns
- Glob-style pattern matching (`*/api/*`, `*/guides/*`)
- Early termination for excluded branches (faster crawling)
- Example: `docpull https://aptos.dev --include-paths "build/guides/*" --exclude-paths "*/changelog"`

**7. Content Filtering** (`--exclude-sections`)
- Remove verbose sections by header name (Examples, Changelog, Full Response, etc.)
- Regex-based content filtering and truncation (future expansion)
- Keep schemas and reference docs, remove bloated examples
- Applied during post-processing after download
- Example: `docpull https://aptos.dev --exclude-sections "Examples" "Full Response" "Changelog"`

**8. Format Conversion** (`--format`)
- **markdown** (default): Standard markdown with YAML frontmatter
- **toon**: Terser Object Oriented Notation (40-60% size reduction, optimized for LLMs)
- **json**: Structured JSON with sections, headers, and metadata
- **sqlite**: Searchable database with FTS5 full-text search
- Example: `docpull https://docs.anthropic.com --format toon` or `--format sqlite`

**9. Smart Naming** (`--naming-strategy`)
- **full** (default): Preserve complete path structure with domain prefix
- **short**: Remove domain prefix, keep directory structure
- **flat**: Single directory with descriptive hyphenated names
- **hierarchical**: Smart hierarchy based on common documentation patterns
- Example: `docpull https://docs.anthropic.com --naming-strategy hierarchical`

#### Phase 3: Advanced Features

**10. Metadata Extraction** (`--extract-metadata`)
- Extract titles, URLs, word counts, categories, last updated dates
- Aggregate statistics: total files, total size, file types, categories
- Output to metadata.json for analysis, search indexing, or documentation audits
- Example: `docpull https://docs.anthropic.com --extract-metadata`

**11. Update Detection** (`--check-updates`, `--update-only-changed`)
- Check which files have changed without downloading
- Only fetch modified files based on checksums, ETags, Last-Modified headers
- Manifest tracking with automatic cache management
- Saves bandwidth and time on regular documentation updates
- Example: `docpull https://docs.anthropic.com --update-only-changed`

**12. Incremental Mode** (`--incremental`, `--resume`, `--cache-dir`, `--clear-cache`)
- Resume interrupted downloads from checkpoint
- State persistence across sessions
- Cache directory for manifests and state files
- Essential for large documentation sets (1000+ files)
- Example: `docpull https://aptos.dev --incremental --resume`

#### Phase 4: Extensibility

**13. Hooks & Plugins** (`--post-process-hook`, `--pre-fetch-hook`)
- Python plugin system for custom processing
- Hook types: `pre_fetch`, `post_fetch`, `post_process`, `filter`
- Decorator-based hook registration (`@hook(HookType.POST_PROCESS)`)
- Load hooks from Python files
- Example: `docpull https://docs.anthropic.com --post-process-hook ./optimize.py`

**14. Git Integration** (`--git-commit`, `--git-message`, `--git-tag`, `--git-author`)
- Automatically commit documentation changes after successful fetch
- Customizable commit messages with templates (`{date}`, `{datetime}`)
- Optional tagging for versioned snapshots
- Track documentation evolution over time
- Example: `docpull --sources-file sources.yaml --git-commit --git-message "Update docs - {date}"`

**15. Archive Mode** (`--archive`, `--archive-format`, `--archive-name`)
- Create compressed archives of documentation
- Formats: tar.gz (default), tar.bz2, tar.xz, zip
- Date-stamped archives for distribution
- Single-file documentation bundles
- Example: `docpull https://docs.anthropic.com --archive --archive-format tar.gz`

### Added - New Modules

- `docpull/processors/`: Post-processing pipeline
  - `base.py`: BaseProcessor interface and ProcessorPipeline
  - `language_filter.py`: Language filtering processor
  - `deduplicator.py`: Deduplication processor with hash-based detection
  - `size_limiter.py`: Size limit enforcement
  - `content_filter.py`: Section and content filtering
- `docpull/formatters/`: Output format converters
  - `base.py`: BaseFormatter interface
  - `markdown.py`: Markdown formatter (default)
  - `toon.py`: TOON format converter (compact for LLMs)
  - `json.py`: JSON formatter with structured sections
  - `sqlite.py`: SQLite database with FTS5 search
- `docpull/indexer.py`: Auto-index generation with tree/TOC/categories/stats
- `docpull/naming.py`: Smart naming strategies (full, short, flat, hierarchical)
- `docpull/metadata.py`: Metadata extraction and aggregation
- `docpull/cache.py`: Cache management for update detection and incremental fetching
- `docpull/hooks.py`: Plugin/hook system with decorator support
- `docpull/vcs.py`: Git integration (commit, tag, status, diff)
- `docpull/archive.py`: Archive creation (tarball, zip)
- `docpull/sources_config.py`: Multi-source YAML configuration with per-source settings
- Enhanced `docpull/cli.py`: Integrated all new CLI arguments with organized argument groups

### Changed - New Required Dependencies

**IMPORTANT**: This release adds new required dependencies for enhanced functionality.

1. **PyYAML is now a REQUIRED dependency** (was optional in v1.1.0)
   - Required for `--sources-file` multi-source configuration
   - Automatically installed with: `pip install --upgrade docpull`

2. **GitPython is now a REQUIRED dependency** (new in v1.2.0)
   - Required for `--git-commit` git integration features
   - Automatically installed with: `pip install --upgrade docpull`

**Backward Compatibility**: All existing CLI commands and workflows continue to work. New features are purely additive.

### Changed - Improvements

- CLI organized into logical argument groups (Multi-Source, Language Filtering, Deduplication, Size Limits, Content Filtering, Output Format, Index Generation, Metadata, Update Detection, Hooks, Git Integration, Archive Mode)
- Enhanced configuration schema to support all 15 new features
- Better error messages and validation throughout
- Structured logging with feature-specific messages
- Comprehensive documentation and examples

### Performance Improvements

Real-world optimization results from testing with 1,914 files (31 MB):
- **Language filtering**: -352 files, -5-10 MB (Claude Code docs in 9 languages → English only)
- **Deduplication**: -304 files, -10 MB (Aptos Move references across 3 environments)
- **Size limits**: -3-5 MB (Skip verbose API examples over 200 KB)
- **Content filtering**: Additional KB savings by removing Changelog/Examples sections
- **Combined optimizations**: 31 MB → ~13 MB (58% reduction)

### Documentation

- Comprehensive CHANGELOG with feature descriptions and real-world impact
- Updated README with all 15 features and usage examples
- Migration guide for v1.x users
- Example `sources.yaml` configuration file
- Hook development guide and examples

### Testing

- Unit tests for all new processor modules (language_filter, deduplicator, size_limiter, content_filter)
- Unit tests for all formatters (markdown, TOON, JSON, SQLite)
- Unit tests for indexer, naming, metadata extraction, cache management
- Unit tests for hooks system, git integration, archive creation
- Integration tests for multi-source workflows
- Mock-based tests for external dependencies (git, sqlite)

---

## Example: What You Can Now Do

### Before v1.2.0 (Manual Process):
```bash
docpull https://docs.anthropic.com --output-dir ./docs/anthropic
docpull https://code.claude.com/docs --output-dir ./docs/claude-code
docpull https://aptos.dev --output-dir ./docs/aptos
docpull https://shelby.xyz --output-dir ./docs/shelby
# Then manually run optimization scripts
# Result: 31 MB, 1,914 files, no navigation
```

### After v1.2.0 (One Command):
```bash
docpull --sources-file docs-config.yaml
```

**docs-config.yaml:**
```yaml
sources:
  anthropic:
    url: https://docs.anthropic.com
    language: en
    max_file_size: 200kb
    create_index: true

  claude-code:
    url: https://code.claude.com/docs
    language: en          # Skips 352 translation files!
    create_index: true

  aptos:
    url: https://aptos.dev
    deduplicate: true
    keep_variant: mainnet  # Skips 304 duplicates!
    max_file_size: 200kb
    include_paths: ["build/*"]

  shelby:
    url: https://docs.shelby.xyz
    create_index: true

git_commit: true
git_message: "Update docs - {date}"
```

**Result**: ~13 MB (58% smaller), all indexes created, one command, repeatable, version-controlled!

---

## [1.1.0] - 2025-11-14

### Added
- `--doctor` command for diagnosing installation and dependency issues
  - Checks all core dependencies (requests, beautifulsoup4, html2text, defusedxml, aiohttp, rich)
  - Checks optional dependencies (PyYAML, Playwright) with installation suggestions
  - Tests network connectivity
  - Verifies output directory write permissions
  - Works even when dependencies are missing
- `requirements.txt` file for transparent dependency listing
- Comprehensive `TROUBLESHOOTING.md` documentation with:
  - Installation troubleshooting (missing dependencies, pipx issues)
  - Runtime issue solutions (YAML config errors, JavaScript rendering)
  - Diagnostic tools usage guide
  - Common error messages reference table
  - Quick reference commands

### Changed
- Improved error handling for missing dependencies
  - Early dependency checking at CLI entry point
  - Clear, actionable error messages with installation instructions
  - Specific recommendations for pipx, pip, and development installations
- Enhanced YAML configuration error handling
  - Auto-fallback to JSON when PyYAML is not installed
  - Clear error messages for YAML-related import errors
  - Helpful suggestions for installing optional dependencies
- Updated README.md with:
  - `--doctor` command in Quick Start section
  - Reference to TROUBLESHOOTING.md
  - Better troubleshooting guidance

### Fixed
- Improved user experience when dependencies are missing (no more confusing tracebacks)
- Better handling of optional dependency errors (PyYAML, Playwright)

## [1.0.0] - 2025-11-07

### Added
- Initial release of docpull
- Support for fetching documentation from multiple sources:
  - Stripe API documentation
  - Plaid API documentation
  - Next.js documentation
  - D3.js documentation (devdocs.io)
  - Bun runtime documentation
  - Tailwind CSS documentation
  - React documentation
- CLI interface with config file support (YAML/JSON)
- Parallel fetching with ThreadPoolExecutor for improved performance
- Security features:
  - Path traversal protection
  - XXE (XML External Entity) protection
  - File size limits (50MB default)
  - Redirect limits (5 hops)
  - Request timeouts (30s)
  - HTTPS enforcement with certificate verification
- Rate limiting to respect server resources
- Structured logging with configurable levels
- YAML frontmatter metadata in generated markdown files
- Config file generation command
- Extensible fetcher architecture for easy addition of new sources
- Comprehensive documentation and examples

### Changed
- Cleaned up README to remove emojis and update to organization URLs
- Applied 2025 PyPI best practices to packaging configuration
- Reorganized project structure for better maintainability

### Security
- Implemented multiple security layers for safe web scraping
- Added security scanning with Bandit and pip-audit
- Created GitHub Actions workflow for automated security checks
- Documented security features in SECURITY.md

---

[1.1.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.1.0
[1.0.0]: https://github.com/raintree-technology/docpull/releases/tag/v1.0.0

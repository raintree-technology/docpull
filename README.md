# docpull

**Pull documentation from any website and convert it to clean, AI-ready Markdown.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/docpull.svg)](https://badge.fury.io/py/docpull)
[![Downloads](https://pepy.tech/badge/docpull)](https://pepy.tech/project/docpull)
[![License: MIT](https://img.shields.io/github/license/raintree-technology/docpull)](https://github.com/raintree-technology/docpull/blob/main/LICENSE)

## Install

```bash
pip install docpull
```

## Usage

```bash
# Basic fetch
docpull https://docs.example.com

# With options
docpull https://aptos.dev --max-pages 100 --output-dir ./docs

# Filter paths
docpull https://docs.example.com --include-paths "/api/*" --exclude-paths "/changelog/*"

# Enable caching for incremental updates
docpull https://docs.example.com --cache

# JavaScript-heavy sites
pip install docpull[js]
docpull https://spa-site.com --js
```

## Profiles

```bash
docpull https://site.com --profile rag      # Optimized for RAG/LLM (default)
docpull https://site.com --profile mirror   # Full site archive with caching
docpull https://site.com --profile quick    # Fast sampling (50 pages, depth 2)
```

## Options

```
Crawl:
  --max-pages N           Maximum pages to fetch
  --max-depth N           Maximum crawl depth
  --include-paths P       Only crawl matching URL patterns
  --exclude-paths P       Skip matching URL patterns
  --js                    Enable JavaScript rendering

Cache:
  --cache                 Enable caching for incremental updates
  --cache-dir DIR         Cache directory (default: .docpull-cache)
  --cache-ttl DAYS        Days before cache expires (default: 30)

Content:
  --streaming-dedup       Real-time duplicate detection
  --language CODE         Filter by language (e.g., en)

Output:
  --output-dir, -o DIR    Output directory (default: ./docs)
  --dry-run               Show what would be fetched
  --verbose, -v           Verbose output
```

See `docpull --help` for all options.

## Python API

```python
import asyncio
from docpull import Fetcher, DocpullConfig, ProfileName, EventType

async def main():
    config = DocpullConfig(
        url="https://docs.example.com",
        profile=ProfileName.RAG,
        crawl={"max_pages": 100},
        cache={"enabled": True},
    )

    async with Fetcher(config) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_PROGRESS:
                print(f"{event.current}/{event.total}: {event.url}")

        print(f"Done: {fetcher.stats.pages_fetched} pages")

asyncio.run(main())
```

## Output

Each page becomes a Markdown file with YAML frontmatter:

```markdown
---
title: "Getting Started"
source: https://docs.example.com/guide
---

# Getting Started
...
```

## Security

- HTTPS-only, mandatory robots.txt compliance
- Blocks private/internal network IPs
- Path traversal and XXE protection

## Troubleshooting

```bash
docpull --doctor              # Check installation
docpull URL --verbose         # Verbose output
docpull URL --dry-run         # Test without downloading
```

## Links

- [PyPI](https://pypi.org/project/docpull/)
- [GitHub](https://github.com/raintree-technology/docpull)
- [Changelog](https://github.com/raintree-technology/docpull/blob/main/docs/CHANGELOG.md)

## License

MIT

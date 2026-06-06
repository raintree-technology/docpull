# Functionality Matrix

| Feature | Documented? | Implemented? | Tested? | Verified by audit? | Evidence | Gaps | Priority |
|---|---:|---:|---:|---:|---|---|---|
| Package version 4.0.0 | Yes | Yes | Partial | Yes | `pyproject.toml:6-7`, `pip show docpull` | Runtime CLI blocked in dirty worktree | P0 |
| CLI `--version` | Yes | Yes | Yes | Broken in worktree | `cli.py:76-80`; command fails importing `FetchEvent` | Fix import blocker | P0 |
| CLI `--help` | Yes | Yes | Partial | Broken in worktree | `cli.py:48-369`; command fails importing `FetchEvent` | Add no-import-regression smoke | P0 |
| CLI `--doctor` | Yes | Yes | Unknown | Broken in worktree | `cli.py:10-20`, `doctor.py`; command fails before doctor path through script import | Make doctor import-light | P0 |
| `--single` | Yes | Yes | Partial | Static only | `README.md:44-45`, `cli.py:97-101`, `cli.py:547-565` | Runtime blocked | P0 |
| Profiles `rag/mirror/quick/llm` | Yes | Yes | Yes | Partial | `README.md:124-131`, `profiles.py:9-69` | LLM fail-loud claim mismatch | P0 |
| Markdown output | Yes | Yes | Yes | Static only | `OutputConfig.format` in `config.py:140-144`, `SaveStep` | Runtime blocked | P0 |
| JSON output | Yes | Yes | Yes | Static only | `cli.py:129-134`, `JsonSaveStep` present | Schema docs thin | P1 |
| NDJSON/stream output | Yes | Yes | Yes | Static only | `README.md:237-241`, `save_ndjson.py:25-69` | Runtime blocked | P0 |
| SQLite output | Yes | Yes | New tests | Static only | `config.py:141`, `save_sqlite.py`, `test_save_sqlite.py` | Worktree changes blocked; docs minimal | P0 |
| Cache/incremental | Yes | Yes | Yes | Static only | `cli.py:312-342`, `FetchStep` conditional headers | Runtime blocked | P0 |
| Resume/frontier | Site/docs claim | Worktree partial | New tests | Broken/unverified | `web/components/Features.tsx:18-20`, dirty `frontier.py` | Import failure blocks; public HEAD may not include frontier | P0 |
| SSRF controls | Yes | Yes | Yes | Static + tests present | `url_validator.py:48-197`, `test_security_hardening.py` | Need decompression/proxy edge tests | P1 |
| DNS pinning | Yes | Yes | Yes | Static | `http/client.py:32-76`, `robots.py:47-76` | Proxy mode weaker by design | P1 |
| robots.txt mandatory | Yes | Yes | Yes | Static | `validate.py:96-107`, `robots.py:193-203` | Crawl-delay not clearly enforced from robots | P1 |
| Sitemap discovery | Yes | Yes | Yes | Static | `sitemap.py:23-43`, tests in `test_discovery.py` | Size checked after response already in memory | P1 |
| Link crawling | Yes | Yes | Yes | Static | `crawler.py:25-42`, `143-229` | Need canonical URL behavior docs/tests | P2 |
| Next.js extraction | Yes | Yes | Yes | Static | `README.md:59-65`, `special_cases.py:85-171` | App Router coverage partial | P2 |
| Mintlify extraction | Yes | Yes | Yes | Static | `special_cases.py:193-214` | Delegates to Next only | P2 |
| Docusaurus/Sphinx | Yes | Partial | Partial | Partial | Docusaurus detector returns `None` intentionally at `special_cases.py:174-190`; README says tagged/generic | Sphinx implementation not evident in inspected snippet; claim needs direct test/code trace | P1 |
| OpenAPI extraction | Yes | Yes | Yes | Static | `special_cases.py:306-360` | Needs Redoc/Scalar variants | P2 |
| JS-only detection | Yes | Yes | Yes | Static | tests `test_spa_detected_and_skipped`, `test_strict_js_required_raises_error` | LLM profile not strict despite comment | P0 |
| Rich metadata | Yes | Yes | Yes | Static | `metadata_extractor.py:36-92` | Potential extruct parser limits not documented | P1 |
| YAML frontmatter hardening | Yes | Yes | Yes | Static | `markdown.py:218-277`, changelog `docs/CHANGELOG.md:28-30` | Add fuzz/regression corpus | P1 |
| Python API `fetch_one` | Yes | Yes | Partial | Broken in worktree | `README.md:83-122`, `__init__.py`, `fetcher.py` | Import failure blocks | P0 |
| Python MCP 8 tools | Yes | Yes | Yes | Static | `README.md:184-198`, `server.py:225-485`, `test_mcp_server.py` | Runtime blocked | P0 |
| MCP structuredContent | Yes | Yes | Yes | Static | `server.py:596-599`, schemas in `server.py:54-183` | Errors intentionally no structuredContent | P1 |
| Claude plugin slash commands | Yes | Yes | Partial | Static | `plugin/README.md:7-18`, `plugin/commands/*` | Cache path docs wrong | P0 |
| Root TypeScript MCP mirror | Yes | Yes in-tree | Some TS tests | Unverified public mirror | `README.md:211-219`, `mcp/package.json:1-19` | Public repo unavailable/unverified | P1 |
| Performance 10k benchmark | Yes | Yes | Yes | Not run | `.github/workflows/benchmark.yml`, `tests/benchmarks/test_10k_pages.py` | Import failure prevents local run | P0 |

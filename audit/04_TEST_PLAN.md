# Test Plan

## Current Inventory

- Python source files: 60.
- Python test files: 22.
- Test definitions/classes counted by grep: 161.
- Categories present: CLI, config, integration, discovery, link extractors, conversion, special cases, pipeline, chunking, cache conditional GET, MCP tools/server, security hardening, CI policy, real-site regressions, benchmark/performance, SQLite/NDJSON in dirty worktree.

## Baseline Results

- `pytest -q`: failed during collection with 11 errors, all rooted in `NameError: name 'FetchEvent' is not defined`.
- `pytest --cov=src/docpull --cov-report=term-missing`: failed during collection for same reason.
- `ruff check .`: failed with 11 issues, including `F821 Undefined name FetchEvent`, `F821 Undefined name SkipReason`, import sorting, line length, and unused import.
- `mypy src/docpull`: failed with 3 errors in dirty worktree.
- `bandit -r src/docpull`: found 8 low-severity `assert_used` findings; pyproject has a documented skip policy for B101 at `pyproject.toml:182-197`, but the local command did not pass `-c pyproject.toml`.
- `pip-audit`: failed due DNS resolution to `pypi.org`.

## Missing or Weak Test Areas

- CLI no-network smoke: import, `--version`, `--help`, `--doctor`, `mcp --help`.
- Profile contract tests: `llm` strict JS behavior vs docs, mirror hierarchical naming expectation, quick profile page/depth caps.
- Output format end-to-end tests: markdown, JSON, NDJSON, SQLite with small local server.
- Cache/resume end-to-end tests: interrupted crawl, frontier persistence, stale fingerprint, output-dir deleted with cache retained.
- Security edge tests: decompression bombs, root-dot hostnames in redirects, wildcard rebinding domains in Python validator, IPv4-mapped IPv6 redirects, symlinked output/cache directories, proxy + pinned DNS behavior.
- MCP integration: all 8 tools through official client, including add/remove source, grep/read roundtrip, progress token, structured content validation.
- Plugin bundle tests: `.claude-plugin` metadata, commands match tool names, cache path docs, slash command UX.
- TypeScript MCP: semantic search disabled/enabled paths, DB migration tests, docpull child-process timeout, stale version field.
- Performance tests runnable locally with opt-in env and summary artifact.

## Prioritized Regression Suite

P0:
- `tests/test_cli_smoke.py::test_version_imports_cleanly`
- `tests/test_cli_smoke.py::test_help_imports_cleanly`
- `tests/test_cli_smoke.py::test_doctor_imports_cleanly`
- `tests/test_profiles.py::test_llm_profile_matches_documented_js_policy`
- `tests/test_cli.py::test_cli_naming_strategy_choices_match_config_literal`
- `tests/test_plugin_docs.py::test_plugin_readme_cache_path_matches_sources_default`

P1:
- `tests/test_outputs_e2e.py::test_markdown_output_local_server`
- `tests/test_outputs_e2e.py::test_json_output_schema_local_server`
- `tests/test_outputs_e2e.py::test_ndjson_stream_stdout_flushes_per_page`
- `tests/test_outputs_e2e.py::test_sqlite_output_schema_and_migration`
- `tests/test_security_hardening.py::test_python_validator_blocks_wildcard_rebinding_domains_or_documents_allowance`
- `tests/test_security_hardening.py::test_redirect_to_ipv4_mapped_private_ipv6_blocked`
- `tests/test_security_hardening.py::test_proxy_with_require_pinned_dns_rejected`
- `tests/test_security_hardening.py::test_oversized_sitemap_stream_aborts_before_full_read`

P2:
- `tests/test_framework_extractors.py::test_mkdocs_material_fixture`
- `tests/test_framework_extractors.py::test_vitepress_fixture`
- `tests/test_framework_extractors.py::test_starlight_fixture`
- `tests/test_framework_extractors.py::test_gitbook_fixture`
- `tests/test_framework_extractors.py::test_redoc_scalar_openapi_fixture`
- `tests/test_mcp_server.py::test_all_tools_have_annotations_and_output_schemas`
- `tests/test_mcp_server.py::test_progress_token_forwarding`
- `mcp/src/server.test.ts::ensure_docs_times_out_child_process`

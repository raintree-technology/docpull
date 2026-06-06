# Claim / Implementation Gaps

| Claim | Source | Actual Evidence | Status | Fix |
|---|---|---|---|---|
| Current runtime supports CLI help/version/doctor | README quickstart and CLI docs | Current worktree commands fail importing `FetchEvent` | Broken | Fix annotation/import blocker and add smoke tests |
| LLM profile is fail-loud on JS-only pages | `profiles.py:47-48` comment and README agent positioning | `strict_js_required=False` at `profiles.py:54-57` | Gap | Align docs/comment/config |
| `flat`/`short` naming aliases removed in 3.0 | `docs/CHANGELOG.md:118-120` | CLI still accepts them at `cli.py:136-143`; config rejects them | Gap | Remove from CLI choices |
| Plugin cache path is `$XDG_DATA_HOME/docpull/docs` | `plugin/README.md:63-65` | Python MCP uses `docpull-mcp/docs` at `sources.py:114-120` | Gap | Update plugin README |
| Plugin prerequisite should print `2.5.0 or newer` | `plugin/README.md:27` | Package is 4.0.0; plugin metadata version 0.2.0 | Stale | Update prerequisite/version docs |
| Root `mcp/` mirrored to public `docpull-mcp` repo | `README.md:211-219`, `mcp/package.json:35-38` | Public mirror not verified in browser/search | Unverified | Make repo public or mark private/unavailable |
| TypeScript MCP server version is 0.3.0 | `mcp/package.json:2-3` | `mcp/src/server.ts:396` says version `0.2.0` | Gap | Single-source version |
| Website says discovered URL list is persisted for crash resume | `web/components/Features.tsx:18-20` | Dirty worktree has `frontier.py`, but import failure blocks verification | Unverified/Broken | Stabilize frontier and add e2e resume test |
| Sphinx detected/tagged | `README.md:64-65` | No directly inspected Sphinx extractor evidence in `special_cases.py` snippet | Unverified | Add explicit Sphinx test/code reference or remove claim |
| Production/stable classifier | `pyproject.toml:24-27` | Dirty worktree is not runnable | Gap for current workspace | Do not release until clean gates pass |
| All tools with data return structuredContent | `README.md:198` | Implemented for most tools; `fetch_url` intentionally has no output schema | Mostly accurate | Wording should say all schema-backed tools |
| Website/product says production-grade ingestion | `web/components/Features.tsx:37-39` | Strong design, but current worktree broken | Gap for current workspace | Ship patch after gates |

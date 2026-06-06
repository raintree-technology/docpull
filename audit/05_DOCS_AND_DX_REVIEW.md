# Docs and DX Review

## Accurate Claims

- Install extras in README match `pyproject.toml` extras for `llm`, `trafilatura`, `mcp`, and `all`.
- README accurately describes Python MCP 8-tool surface at `README.md:184-198`, matching `src/docpull/mcp/server.py:225-485`.
- README security claims are mostly backed by code: SSRF, DNS pinning, XXE, CRLF, redirect auth stripping.
- Website feature copy around zero-trust networking is backed by `UrlValidator` and `AsyncHttpClient`.
- Changelog 4.0.0 security claims are substantially traceable to code.

## Claim / Implementation Gaps

- README quickstart and CLI examples cannot currently run in dirty worktree due import failure.
- LLM profile comment says "fail-loud on JS-only pages" in `profiles.py:47-48`, but config sets `strict_js_required=False` in `profiles.py:54-57`.
- README advertises `docpull mcp` startup, but current worktree import failure blocks it.
- CLI help still accepts `flat` and `short` naming aliases, contradicting changelog 3.0.0 removal and `OutputConfig` literal.
- Plugin README says cache defaults to `docpull/docs`; implementation uses `docpull-mcp/docs`.
- Plugin README prerequisite says `docpull --version` should print `2.5.0 or newer`; current package is `4.0.0`, so this is stale.
- README claims root `mcp/` mirror exists publicly; unable to verify public repo.
- Website says discovered URL list is persisted and crash resumes in `web/components/Features.tsx:18-20`; current worktree contains frontier code but runtime is broken, and public-release behavior needs clean verification.

## New User Friction

- If installing from this dirty source tree, every CLI command fails before help.
- `python` command is absent on this macOS environment; docs use `python -m venv`, which may require `python3`.
- Network-restricted environments cannot run `pip install -e ".[all,dev]"` because build isolation tries to resolve setuptools from PyPI.
- `docpull --doctor` is intended as a diagnostic but is currently blocked by package import path in script execution.
- SQLite output is accepted by CLI/config but barely documented.
- Authenticated docs are supported in config/CLI, but safe usage examples and secret-scoping guidance are thin.

## AI-Agent DX Friction

- MCP tool docs are good, but plugin slash commands rely on accurate cache/source path docs.
- `grep_docs` is regex-only; agents may choose brittle patterns and miss relevant content.
- No stable manifest schema/chunk IDs, making regenerated corpora hard to diff or cite.
- No explicit crawl budget explanation in MCP responses before fetching large sources.

## Packaging

- `pyproject.toml` declares Python 3.10-3.14 and typed package via `py.typed`.
- CI tests Python 3.10-3.13 but intentionally excludes 3.14 in `.github/workflows/ci.yml`.
- Publish workflow uses tag-only trusted publishing and verifies tag matches `pyproject.toml`.
- Actions are pinned to full SHAs in inspected workflows.
- Issue template contact links still point to `raintree-ai/docpull`, not `raintree-technology/docpull`.

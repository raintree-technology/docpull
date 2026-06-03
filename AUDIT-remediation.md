# docpull Audit and Remediation Plan

Audit date: 2026-06-03
Scope: `/Users/mb1/Code/secondary/docpull`
Commit audited: `14b51d1bd9a010b194a2fa2d1a25c5fb9e621c64` (`main`, `origin/main`, tag `v3.0.2`)

## Executive Summary

No Critical issues found in the core crawler. The crown-jewel protections are present and covered by tests: HTTPS-only validation, private/link-local/loopback blocking, connect-time DNS pinning, redirect re-validation, robots fail-closed behavior, CRLF header guards, content-size caps, and path validation on crawler writes.

The main remediation work is supply-chain hardening and one MCP file-access edge:

- High: GitHub Actions and the gitleaks container are not pinned to full immutable SHAs/digests.
- High: `pip-audit` currently fails because the Python MCP extra pulls `mcp 1.27.0`, which pulls vulnerable `PyJWT 2.12.1`.
- Medium: Python MCP `grep_docs` follows Markdown symlinks in the local docs cache, while `read_doc` already rejects symlink escapes.
- Medium/Low: hostile XML parsing has safe implementation via `defusedxml`, but no explicit XXE/billion-laughs regression test was found.

`web/` has expected uncommitted work and was not modified.

## Verification Run

Passed:

- `.venv/bin/python -m ruff check src tests`
- `.venv/bin/python -m mypy src`
- `.venv/bin/python -m pytest tests -q` -> `325 passed in 11.90s`
- `.venv/bin/python -m bandit -q -c pyproject.toml -r src`
- `bun test` in `mcp/` -> `10 pass`
- `bun run typecheck` in `mcp/`
- `bun audit` in `mcp/` -> no vulnerabilities
- `npm audit --omit=dev` in `web/` -> no vulnerabilities

Failed / notable:

- `python` is not on PATH; `python3` exists but lacks project dev packages. The project `.venv/bin/python` is the usable interpreter.
- `.venv/bin/python -m pip_audit` failed:
  - `pyjwt 2.12.1` has `PYSEC-2026-175`, `PYSEC-2026-177`, `PYSEC-2026-178`, `PYSEC-2026-179`; fixed in `2.13.0`.
  - `pip show` shows `PyJWT` is required by `mcp`, not directly by docpull.
- `git ls-remote https://github.com/raintree-technology/docpull-mcp` could not verify the mirror because local git invokes a missing `gh` credential helper. The browser view of that URL returns 404, so MCP mirror drift is not verified.

## Drift Notes

- The prompt says recent hardening pinned CodeQL and all actions to full SHAs. Current workflows still use tag refs such as `actions/checkout@v6`, `github/codeql-action/init@v3`, `pypa/gh-action-pypi-publish@release/v1`, and `ghcr.io/gitleaks/gitleaks:latest`.
- `mcp/` remains separate from the Python `docpull[mcp]` stdio server. `pyproject.toml` `[mcp]` resolves to Python packages (`mcp`, `python-multipart`, `starlette`), and README clearly distinguishes the root `mcp/` TypeScript pgvector server.
- No headless browser dependencies or implementation paths found. Mentions of "browser" are documentation/tests/web dependency metadata only.

## Findings

### Critical

None found.

### High

#### H1. CI and release workflows use mutable action refs and an unpinned gitleaks image

Files:

- `.github/workflows/ci.yml:28`, `:31`, `:47`, `:56`, `:59`, `:83`, `:86`
- `.github/workflows/benchmark.yml:33`, `:36`, `:65`
- `.github/workflows/codeql.yml:30`, `:33`, `:39`
- `.github/workflows/metrics.yml:45`, `:47`, `:60`
- `.github/workflows/publish.yml:30`, `:32`, `:77`, `:91`, `:96`
- `.github/workflows/security.yml:17`, `:27`, `:34`, `:37`, `:62`, `:65`, `:88`, `:91`

Evidence:

- `rg "uses:\s*[^@\s]+@" .github/workflows` returns tag refs across all workflows.
- `security.yml:27` runs `ghcr.io/gitleaks/gitleaks:latest`.

Impact:

- A compromised action tag or mutable container tag can poison CI or release artifacts. This is especially important for `publish.yml`, which has `id-token: write` for PyPI Trusted Publishing.

Proposed fix:

- Pin every `uses:` entry to a full commit SHA.
- Pin gitleaks to a digest (`ghcr.io/gitleaks/gitleaks@sha256:...`) or install a pinned CLI release with checksum verification.
- Add a lightweight CI check that fails on non-SHA action refs and `:latest` containers.

Risk:

- Low implementation risk, but requires choosing and recording exact SHAs/digests. Dependabot may need config/supporting process to update pinned actions.

#### H2. Python dependency audit fails due vulnerable PyJWT pulled by the MCP SDK

Files:

- `pyproject.toml:91-95`
- `.github/workflows/publish.yml:67`
- `.github/workflows/security.yml:47`

Evidence:

- `.venv/bin/python -m pip_audit` reports four advisories against `pyjwt 2.12.1`, fixed in `2.13.0`.
- `.venv/bin/python -m pip show pyjwt mcp` shows `PyJWT 2.12.1` is `Required-by: mcp`; `mcp 1.27.0` is installed in `.venv`.
- `publish.yml` and `security.yml` both run `pip-audit`, so release/security gates can fail or drift depending on resolver timing.

Impact:

- Published MCP users may receive a vulnerable transitive auth/JWT dependency even though docpull only uses stdio MCP.
- Release and security workflows are currently brittle.

Proposed fix:

- Add a direct lower bound/constraint in the `[mcp]` and `[all]` extras, for example `pyjwt>=2.13.0`, if compatible with `mcp`.
- Re-run `pip-audit`, stdio MCP smoke test, and full Python tests.

Risk:

- Low to medium. Needs resolver validation across Python 3.10-3.13 and the MCP smoke test.

### Medium

#### M1. Python MCP `grep_docs` can read symlinked Markdown files that escape the docs cache

File:

- `src/docpull/mcp/tools.py:452-460`

Evidence:

- `grep_docs` iterates `root.rglob("*.md")` and calls `file.read_text(...)`.
- `read_doc` correctly resolves `target` and rejects paths outside `library_root` at `tools.py:588-592`.
- The TypeScript MCP ingestion path explicitly skips symlinks at `mcp/src/ingest.ts:171-176`, so the safer pattern already exists elsewhere.

Impact:

- If a symlink named `*.md` is planted under a cached docs library, `grep_docs` can surface content outside the docs cache. This is local-file exposure through an MCP read tool.
- The normal crawler write path resists this because `SaveStep._validate_output_path()` resolves output paths before writing, but MCP caches should still not trust existing filesystem entries.

Proposed fix:

- In `grep_docs`, skip symlinks and resolve each candidate before reading:
  - reject if `file.is_symlink()`
  - require `file.resolve().relative_to(root.resolve())`
- Add a regression test with `tmp_path/lib/leak.md -> tmp_path/secret.md`.

Risk:

- Low. Could hide intentionally symlinked local docs, but the MCP security posture should prefer no symlink traversal.

#### M2. XML parser safety is implemented but missing explicit hostile XML regression coverage

File:

- `src/docpull/discovery/sitemap.py:9`, `:162`
- `tests/test_discovery.py:147-228`

Evidence:

- Sitemaps are parsed with `defusedxml.ElementTree.fromstring`, which is the right primitive.
- Existing discovery tests cover simple sitemaps, max URL limits, and off-domain sitemap URLs, but search did not find tests for `DOCTYPE`, external entity, or entity expansion payloads.

Impact:

- Low immediate exploit likelihood because implementation uses `defusedxml`.
- Missing regression coverage weakens confidence around a claimed public security feature.

Proposed fix:

- Add tests that feed `_parse_sitemap()` a `DOCTYPE`/external entity payload and a small entity-expansion payload and assert no URL is emitted and no network/file access occurs.

Risk:

- Low.

#### M3. Publish workflow permits manual publish from `main` without a release tag

File:

- `.github/workflows/publish.yml:15-19`, `:36-54`, `:82-99`

Evidence:

- `workflow_dispatch` is enabled.
- The script rejects manual dispatch from non-`main`, but it does not require a `vX.Y.Z` tag for manual dispatch.
- The publish job has PyPI OIDC via environment `pypi`.

Impact:

- A maintainer or compromised account with workflow dispatch access can publish current `main` contents without a tag-to-version release marker. The environment may still require approval, but the workflow itself is less strict than the documented tag discipline.

Proposed fix:

- Remove `workflow_dispatch`, or require manual dispatch to provide a version and verify an existing matching signed/tagged release before publishing.

Risk:

- Medium process risk: this changes release ergonomics.

### Low

#### L1. Unconfigured Bandit command reports asserts unless `pyproject.toml` is passed

File:

- `pyproject.toml:124-143`

Evidence:

- `.venv/bin/python -m bandit -q -r src` reports eight B101 assert findings.
- `.venv/bin/python -m bandit -q -c pyproject.toml -r src` passes.

Impact:

- Not a code vulnerability; the config is intentional and documented.
- Contributors can get noisy local failures if they run the unconfigured command.

Proposed fix:

- Ensure docs/Makefile/pre-commit consistently use `bandit -q -c pyproject.toml -r src`.

Risk:

- Low.

#### L2. MCP mirror divergence could not be verified from this environment

File:

- `README.md:211-220`
- `mcp/package.json:42`

Evidence:

- The repo says `mcp/` is mirrored to `raintree-technology/docpull-mcp`.
- `git ls-remote` failed due missing local `gh` credential helper; public GitHub URL returned 404 in browser.

Impact:

- Unknown. The local repo remains clear about the distinction between Python MCP and TS/pgvector MCP.

Proposed fix:

- Verify mirror state from an authenticated GitHub environment or update docs if the mirror is private/unpublished.

Risk:

- Low.

## Confirmed Security Posture

- SSRF / DNS rebinding:
  - URL validation blocks localhost/internal/private/link-local/reserved/multicast/unspecified/site-local addresses at `src/docpull/security/url_validator.py:108-242`.
  - Direct aiohttp connections use `_ValidatedResolver` with numeric validated addresses at `src/docpull/http/client.py:32-79`.
  - Redirects are re-validated in GET and HEAD paths at `src/docpull/http/client.py:385-400` and `:507-522`.
  - Tests cover public host resolving to loopback, connect-time rebinding, and redirect to metadata IP at `tests/test_security_hardening.py:83-169`.
- CRLF:
  - Header names/values are rejected in config and transport at `src/docpull/models/config.py:218-264` and `src/docpull/http/client.py:176-201`.
  - Tests cover user-agent and auth-header CR/LF/null at `tests/test_security_hardening.py:275-333`.
- Robots:
  - robots.txt uses a pinned HTTPS connection and fail-closed error state at `src/docpull/security/robots.py:147-199`, `:235-284`, `:304-329`.
  - Tests cover unsafe robots redirect, fetch error, parser error, and 404 allow behavior.
- Path writes:
  - URL path segments are sanitized in `src/docpull/core/fetcher.py:82-136`.
  - `SaveStep` resolves and checks output paths under the base output dir at `src/docpull/pipeline/steps/save.py:59-81`.
- MCP surfaces:
  - Python `ensure_docs` rejects direct URLs and resolves aliases from validated builtins/user config.
  - Python `fetch_url` is HTTPS-only and uses the same `Fetcher` path.
  - TS pgvector MCP keeps indexing opt-in and requires `DATABASE_URL` plus `OPENAI_API_KEY`; DB queries are parameterized.
- Plugin:
  - Slash commands restrict allowed tools to docpull MCP tools and do not invoke shell tools.
  - `.mcp.json` launches `docpull mcp`, preserving the Python stdio MCP surface.

## Suspected Dead Code / Cleanup Candidates

Needs confirmation before removal:

- `src/docpull/concurrency/manager.py`: referenced only by comments/search output in this audit; no active imports found.
- `security/01-attack-surface.md`: useful historical artifact, but its line references are stale relative to the current tree and it claims some previous findings were remediated. Consider updating or marking as historical.
- `mcp/node_modules/`, Python `__pycache__/`, `.mypy_cache/`, and `web/.next/` exist in the working tree. They appear generated; confirm they are gitignored and not tracked.

## Recommended Remediation Order

1. Fix H2 first: add the safe `PyJWT` lower bound to MCP extras, then run `pip-audit`, MCP smoke test, and full Python tests.
2. Fix M1: harden `grep_docs` against symlink escapes and add regression coverage.
3. Fix M2: add explicit hostile sitemap XML regression tests.
4. Fix H1: pin workflow action SHAs and gitleaks digest; add a ref-policy check.
5. Decide M3: either remove manual publish or make manual publish require an existing matching release tag.
6. Update docs/Makefile guidance for the configured Bandit command if not already covered.

## Stop Point

Per Phase 0, no remediation has been applied yet. `AUDIT-remediation.md` is the only intentional file change from this audit turn.

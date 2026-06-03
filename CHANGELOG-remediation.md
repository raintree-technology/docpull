# Remediation Changelog

Date: 2026-06-03
Scope: audit follow-up for `AUDIT-remediation.md`

## Changes

- Added `pyjwt>=2.13.0` to the Python MCP extras (`mcp`, `all`) so the MCP SDK transitive dependency resolves above the active `PyJWT 2.12.1` advisories.
- Hardened Python MCP `grep_docs` to skip symlinked Markdown files and reject resolved file candidates outside the resolved library root before reading.
- Added regression tests for `grep_docs` symlink escapes.
- Expanded sitemap XML handling to treat `defusedxml` security exceptions as rejected sitemap input instead of uncaught errors.
- Added hostile sitemap regression tests for external entity and entity expansion payloads.
- Pinned GitHub Actions workflow `uses:` refs to full commit SHAs.
- Pinned the gitleaks container to an immutable image digest.
- Removed `workflow_dispatch` from the PyPI publish workflow so publishing is tag-only.
- Added `tests/test_ci_policy.py` to prevent mutable action refs, `:latest` containers, and manual publish dispatch from returning.

## Rationale

The crawler security posture was already strong, but the release pipeline and MCP cache read path had avoidable gaps. These changes keep protections default-on, preserve the Python/TypeScript MCP separation, and avoid version bumps or publishing.

## Verification

Passing checks run locally:

- `.venv/bin/python -m pytest tests/test_mcp_tools.py::test_grep_docs_skips_symlinked_markdown_escape tests/test_discovery.py::TestSitemapDiscoverer::test_parse_sitemap_rejects_external_entity_payload tests/test_discovery.py::TestSitemapDiscoverer::test_parse_sitemap_rejects_entity_expansion_payload -q`
- `.venv/bin/python -m pip_audit`
- `.venv/bin/python -m ruff check src tests`
- `.venv/bin/python -m mypy src`
- `.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_discovery.py tests/test_security_hardening.py -q`
- `.venv/bin/python -m pytest tests/test_ci_policy.py -q`
- Workflow YAML parse with `yaml.safe_load`

Final full-suite verification is recorded in the assistant response after completion.

## Risk and Rollback

- `pyjwt>=2.13.0`: low risk; rollback by removing the direct lower bound, but `pip-audit` will fail again while the transitive dependency resolves to vulnerable versions.
- `grep_docs` symlink skip: low risk; intentionally stops reading symlinked cached docs. Roll back by reverting the `grep_docs` candidate validation if symlinked local documentation is explicitly desired.
- Sitemap security exception handling: low risk; hostile XML now fails closed as empty sitemap output.
- Workflow pinning: low operational risk; future action updates require changing SHAs deliberately.
- Publish tag-only trigger: medium process change; rollback by restoring `workflow_dispatch`, preferably with an explicit tag/version guard.

## Open Decisions

- Verify the external `raintree-technology/docpull-mcp` mirror from an authenticated GitHub environment; local verification was blocked by a missing `gh` credential helper and the public URL returned 404.
- Decide whether to add a documented action-pin update workflow/process for Dependabot-created GitHub Actions updates.

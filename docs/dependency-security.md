# Dependency security

The 2026-10-10 dependency update raises security floors and refreshes the root,
benchmark, and Bun lockfiles. Install from those locks to reproduce the reviewed
dependency set. The Python SDK, CLI, and core Python support range remain intact.

## Optional parser support

The Unstructured backend requires Python 3.11–3.13 and Unstructured 0.24.1 or
newer. Its patched release excludes Python 3.14. On that interpreter, the
`unstructured`, `parse`, and `all` extras omit Unstructured instead of
resolving an older vulnerable release. An explicit request for that backend
fails with a diagnostic before importing it. Use pypdf, MarkItDown, or
the text backend where supported by the input format.

## Retired benchmark baseline

The optional Crawl4AI benchmark baseline and its CLI adapter were removed because
Crawl4AI requires NLTK, whose current release has no published patch for
[GHSA-8mgp-746c-j5xp](https://github.com/advisories/GHSA-8mgp-746c-j5xp)
as of 2026-10-10. Neither dependency remains in the root or benchmark lockfile.
Readability and Trafilatura remain supported local baselines. Historical
benchmark data remains available. No advisory exclusions were added.

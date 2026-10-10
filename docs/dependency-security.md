# Dependency security

The 2026-10-10 dependency update raises security floors and refreshes the root,
benchmark, and Bun lockfiles. Install from those locks to reproduce the reviewed
dependency set. The Python SDK, CLI, and core Python support range remain intact.

## Optional parser support

The Unstructured backend requires Python 3.11–3.13 and Unstructured 0.24.1 or
newer. Its patched release excludes Python 3.10 and Python 3.14. On those
interpreters, the `unstructured`, `parse`, and `all` extras omit Unstructured
instead of resolving an older vulnerable release. An explicit request for that
backend fails with a diagnostic before importing it. Use pypdf, MarkItDown, or
the text backend where supported by the input format.

## Remaining benchmark advisory

The optional Crawl4AI benchmark baseline depends on NLTK. NLTK 3.10.3 fixes the
recorded critical advisories, but its current release remains affected by
[GHSA-8mgp-746c-j5xp](https://github.com/advisories/GHSA-8mgp-746c-j5xp).
No patched version is published in the package advisory metadata as of
2026-10-10. This dependency belongs to the optional benchmark baseline and is
absent from the updated root dependency lock.

Treat model names and artifact paths passed to that baseline as trusted inputs.
Do not use this note to suppress an audit result or consider the advisory fixed.
Track the remaining remediation in
[dependency issue #148](https://github.com/raintree-technology/docpull/issues/148).

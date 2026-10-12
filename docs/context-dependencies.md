# Context Dependencies

DocPull manages public docs and web sources as reproducible context
dependencies for AI agents. The v6 workflow is local-first: aliases resolve
from a bundled catalog, sources are stored in `docpull.yaml`, and syncs write a
`.docpull/context.lock.json` lockfile.

## Workflow

```bash
docpull init my-agent-context
docpull sources list
docpull add stripe react postgres
docpull install
docpull deps
docpull sync
docpull diff
docpull ci --prepare
docpull export context-pack --target codex
```

`docpull add` accepts HTTPS URLs, bundled aliases, and typed known-source
specs. Aliases are source templates, not hosted packages: after resolution,
`docpull.yaml` contains normal validated HTTPS sources and the existing project
workflow owns sync, diff, review, release, and export behavior. Typed project
sources use the same lifecycle, but sync them directly through the matching
pack lane instead of link discovery.

## Alias Catalog

The bundled catalog includes high-value public docs sources such as `stripe`,
`react`, `nextjs`, `openai`, `postgres`, `rust`, `kubernetes`, `terraform`,
`aws`, and `apple-hig`.

```bash
docpull sources list
docpull sources list --json
```

Each alias records a display title, source URL, homepage, description,
recommended source type, and default discovery preference. Aliases cannot
define auth or credentials. For private sources, add an explicit HTTPS URL with
the existing environment-backed auth options.

## Lockfile

`docpull sync` writes `.docpull/context.lock.json`. `docpull install` validates
the project against that lockfile, or writes a skeleton lockfile when one does
not exist yet.

The lockfile records:

- DocPull version and project name
- resolved aliases and source URLs
- typed source specs such as `pypi:requests`, `rfc:9110`, or a local dataset path
- discovered URLs used by the project
- latest run ID when available
- content hashes and an aggregate hash summary
- a SHA-256 digest for the frozen run archive created by a new sync
- context-pack export metadata when exports are created
- non-secret auth readiness metadata

The lockfile never stores credential values, request headers, cookies, bearer
tokens, or third-party keys. If `docpull.yaml` diverges from the lockfile,
`docpull install` fails clearly so dependency changes are intentional.

Use this to validate dependencies without fetching:

```bash
docpull install
```

Use this to inspect dependency, lockfile, latest run, hash, and export status:

```bash
docpull deps
docpull deps --json
```

Use this to validate and then sync:

```bash
docpull install --sync
```

## Frozen Installation

`docpull install --frozen` restores the captured context without fetching any
source. Each new non-dry sync saves a ZIP archive at
`.docpull/artifacts/<sha256>.zip` and pins its digest in the lockfile.

To restore on another machine:

1. Run `docpull sync` on the source machine to capture the desired context.
2. Transfer `docpull.yaml`, `.docpull/context.lock.json`, and the archive named
   by the lockfile's `artifact.sha256`. Keep the two project files at their
   original relative paths in the new project directory.
3. From that directory, run:

   ```bash
   docpull install --frozen --artifact-dir /path/to/transferred-artifacts
   ```

Omit `--artifact-dir` when the archive is already in `.docpull/artifacts`.
The directory must be local or mounted storage; installation does not download
archives from URLs. The Python equivalent is
`install_project(frozen=True, artifact_dir=Path("/path/to/transferred-artifacts"), root=project_root)`.

Installation verifies the lock and archive digests, stages the run, and rebuilds
the local project index. It preserves captured document, chunk, citation, and
agent-document bytes. If absent from the archive, the run's operational
lock-hash field is filled from the current lock. Source-machine paths in captured
metadata remain historical metadata; index paths point to the restored run.

Missing or changed archives, source drift, unsafe archive paths, and conflicting
existing runs fail before project state changes. Frozen installation never
rewrites the lockfile or substitutes newly fetched content. `--frozen` and
`--sync` are mutually exclusive. Repeating an install verifies the existing run;
it does not overwrite local edits or later artifacts added to that run.

Archives contain retained source content and run artifacts, including any
authorized private content and acquisition output in `_fetch`. They exclude the
project cache, credential configuration, index database, and separate exports.
Transfer them only to recipients allowed to access that content. Exports can be regenerated
with `docpull export context-pack --target codex` after installation.

The archive captures the run at sync time. Later diff, evaluation, or seal files
are not retroactively added to that archive. Existing locks without an artifact
digest remain valid for ordinary installation, which now preserves existing
locks even without local runs. To make an older retained run restorable, export
it with `docpull export context-pack --target codex --run <run-id>`; this pins an
archive if the selected run has none. A lockfile alone cannot recover content
that was never retained.

## Query locked context

`docpull query` retrieves cited passages from one or more projects' locked
archives. It validates each selected lock and archive before returning evidence.
It does not fetch sources or change project files. An archive can be queried
before frozen installation, including on a fresh machine with only the project
manifest, lockfile, and transferred archive.

```bash
# Query the current project's locked context.
docpull query "retry policy" --max-tokens 4000

# Query two locked packs in one response.
docpull query "retry policy" --project ./payments --project ./client --max-tokens 8000

# Restrict evidence to explicit document versions and configured sources.
docpull query "retry policy" --version 2.0 --source client-docs

# Read transferred archives from a local or mounted directory.
docpull query "retry policy" --artifact-dir /path/to/transferred-artifacts
```

Repeat `--project`, `--version`, `--pack-version`, `--source`, or `--artifact-dir`
to provide multiple values. Filters accept exact matches; values within one
filter are alternatives, and different filters apply together. `--version`
matches document `metadata.version`. Documents without that field do not match
a version filter. `--pack-version` matches the locked run ID. Neither filter
resolves semantic version ranges or infers versions from URLs. A project exposes
the run pinned by its current lock; query separate project roots to compare
retained locks. `--limit` caps distinct passages at 10 by default, up to 100.

The Python SDK and MCP call the same implementation:

```python
from docpull import ContextQueryError, query_context

result = query_context(
    "retry policy",
    project_roots=["./payments", "./client"],
    max_tokens=8000,
    versions=["2.0"],
)
```

The MCP tool is `context_query`. Its arguments use the SDK names: `query`,
`project_roots`, `max_tokens`, `versions`, `pack_versions`, `sources`, `limit`,
and `artifact_dirs`. Omit `project_roots` to use the process's current project.

The result is JSON with `schema_version: 1`, `status`, `reason`, `budget`,
`results`, and `omitted_passages`. Each result contains `text`, `citations`, and
`omitted_citations`. Ranking uses local lexical matches. Identical passage text
appears once, with citations from each matching document or pack. Internal
whitespace remains intact. Distinct passages take priority over additional
citations for duplicate text.

`max_tokens` defaults to 4000 and must be at least 256. The budget uses
`utf8_bytes_upper_bound`: one UTF-8 byte counts as one budget token. This is a
conservative bound for byte-based tokenizers, not a model-specific token count.
It covers the complete canonical JSON result, including citations, omission
counts, and budget metadata. The CLI's trailing newline and MCP protocol framing
are excluded. SDK callers can use `docpull.context_query.serialize_query_result`
to reproduce the counted representation. Pretty-printing or adding a prompt
requires additional space. No tokenizer data is downloaded.

Paragraphs and fenced code blocks are returned whole or omitted. `budget.used_tokens`
reports the counted response size. `omitted_passages` counts matching passages
excluded by the budget or result limit; `omitted_citations` counts additional
citations excluded by the budget. When no passage fits, `status` is
`insufficient_evidence` and `reason` is `budget_exhausted`. When no passage
matches the query and filters, the reason is `no_matches`. These statuses
describe retrieval, not whether an answer exists elsewhere.

Each citation identifies the pack, locked run, archive SHA-256, source URL,
document ID, content hash, configured source, and explicit document version.
`record_index` is the one-based document ordinal in the archive's
`documents.ndjson`, ignoring blank lines. `char_start` and `char_end` select the
exact passage from that record's `content` using Unicode character offsets,
with an exclusive end. `line_start` and `line_end` are one-based inclusive
line numbers within that content. Citation IDs remain stable for the same
archive, record, and span.

Missing or tampered archives, invalid locks, and source drift fail the whole
query without partial results. The CLI exits with code 1, the SDK raises
`ContextQueryError`, and MCP returns a tool error. The project artifact directory
is searched before additional directories; a corrupt archive is never silently
replaced. Locks without an archive digest require a new sync or an export of
their retained run, as described under frozen installation.

Each query hashes the pinned archive. When the archive changes, sync writes a
full-text index of its passages to `.docpull/search/<artifact-sha256>.sqlite`.
Queries read that index instead of unpacking and splitting the archive. Queries
only read the index, and only when its stored lock facts match the current lock.
A missing, corrupt, or stale index falls back to scanning the archive, with
identical results. Query never writes project files. Retrieval is lexical; it
does not use semantic embeddings.

## Context CI

Use `docpull ci` to check whether the latest project run is safe for agent
loops. It validates the lockfile, pack score, audit score, coverage confidence,
citation coverage, rights sidecars, eval-grade artifacts, and optional
context predictions:

```bash
docpull ci --prepare
docpull ci --predictions agent-output.jsonl
```

Project thresholds can be set under the optional `ci:` key in `docpull.yaml`.
See [Context CI](context-ci.md) for the full workflow and GitHub Actions
recipe, and [Context Pack Contract v3](context-pack-contract-v3.md) for the
artifact shape Context CI expects.

## Context Packs

Project exports keep the existing artifact names and target formats. The
exported `manifest.json` now includes additive identity fields such as pack
name, run-backed version, source count, source aliases, content hash summary,
and the project lockfile path when present.

```bash
docpull export context-pack --target codex
docpull export context-pack --target openai
docpull export context-pack --target langchain
```

Typed lanes can create the same v3 pack contract for known sources that do not
naturally fit a docs-site crawl: papers, public GitHub repos, npm/PyPI
packages, standards, local datasets, transcripts, and Wikimedia pages. Use the
standalone commands for one-off packs, or add typed specs to a project when
the source should live in `docpull.yaml`.

```bash
docpull paper-pack doi:10.1038/nphys1170 -o packs/papers
docpull repo-pack psf/requests -o packs/repo
docpull package-pack pypi:requests -o packs/package
docpull standards-pack rfc:9110 -o packs/standard
docpull dataset-pack ./metrics.csv -o packs/dataset
docpull transcript-pack ./meeting.vtt -o packs/transcript
docpull wiki-pack wiki:Web_scraping -o packs/wiki

docpull add pypi:requests --type package
docpull add rfc:9110 --type standards
docpull add ./metrics.csv --type dataset
```

Typed project sources do not support `--discover`; the lane source is already
the explicit dependency. Use `docpull pack prepare --eval-grade` or the lane
`--eval-grade` flag before relying on standalone typed packs in agent CI loops.

## Boundary

This release does not add a hosted registry, marketplace, remote install,
accounts, proprietary web index, or hidden paid execution. The public contract
is the local artifact workflow: dependency manifest, lockfile, sync, diff,
citations, validation, Context CI, and agent-ready exports.

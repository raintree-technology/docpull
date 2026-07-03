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

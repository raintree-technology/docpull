# Public contract inventory

DocPull 6.2 freezes the existing artifact envelopes and adds transport-neutral
cross-repository contracts. Bundled schemas live in `src/docpull/schemas/` and
are installed with the Python package.

Use `docpull contracts list`, `docpull contracts show NAME`, or
`docpull contracts export -o schemas/` to inspect them.

## Frozen compatibility envelopes

| Contract | Version | Canonical implementation | Compatibility rule |
|---|---:|---|---|
| Pack metadata | existing v1/v3 | `*.pack.json`, `pack.v3.schema.json` | Readers continue accepting old pack metadata and unknown additive fields. |
| Document record | v3 | `DocumentRecord`, `document.v3.schema.json` | Existing field names, IDs, citations, rights, route, and chunk fields remain readable. |
| Run identity | v1 | `RunIdentity`, `run-identity.v1.schema.json` | Fingerprints remain deterministic and secret-free. |
| Citation map | v1 | `citations.json`, `citation-map.v1.schema.json` | `citation_id`, URL, title, and record citations remain stable; authority and versions are additive. |
| Rights | v1 envelope | `rights.manifest.json`, `rights.v1.schema.json` | Unknown remains the conservative default. |
| Provenance | v1 envelope | `provenance.graph.json`, `provenance.v1.schema.json` | Additive nodes and edges are allowed. |
| Basis | v2 | `basis.ndjson`, `basis.v2.schema.json` | Legacy basis rows normalize to v2. |
| Company Brain | compatibility alias | `company_brain.bundle.json` | Alias remains written and readable; canonical contract is `intelligence.bundle.v1`. |

The freeze means existing required fields are not renamed or removed during the
6.x line. Additive fields and sidecars are allowed. Existing CLI commands and
concrete SDK builders retain their result payloads while also writing the new
generic contracts.

## Cross-repository contracts

### `workflow.request.v1`

A scheduler-neutral invocation containing a stable request ID, workflow name,
input, output location, options, source policy, budget, and replay settings.
The request ID excludes ephemeral timestamps.

### `workflow.result.v1`

The common result for brand, product, styleguide, visual/image, screenshot, and
policy workflows. It contains:

- pack and run identities;
- lifecycle progress events;
- structured warnings and failures;
- budget limit, estimated/actual spend, HTTP/cache counts, browser time, and
  blocked actions;
- SHA-256 request, manifest, legacy-result, and pack hashes;
- replay configuration and compatibility artifact paths.

### `artifact.manifest.v1`

A sorted list of named artifacts with relative path, role, media type, bytes,
and SHA-256. `aggregate_sha256` hashes the canonical sorted entry list. The
manifest does not hash itself or `workflow.result.json`, avoiding circular
identity.

### `intelligence.bundle.v1`

The supported tracker-import contract. It contains pack/run identity, source
snapshots, document versions, precise observations, evidence strength,
confidence, source authority, warnings, and before/after change candidates.
The `bundle_hash` covers the canonical v1 core without the self-referential
bundle ID/hash or legacy Company Brain envelope.

### `change.event.v1`

One idempotent event per changed URL. It carries old/new document IDs and
hashes, precise old/new evidence, separate structural and textual changes, and
semantic candidates classified as pricing, positioning, product, security,
policy, or other. Classifications are candidates requiring review.

## Evidence spans

An evidence span contains the citation and record citation IDs, document ID,
content-hash document version, URL, zero-based `char_start`/`char_end`, exact
text, and exact-text SHA-256. Consumers must verify both the document version
and exact text before promoting an observation.

## Compatibility policy

- JSON consumers must ignore unknown fields.
- DocPull readers continue to accept old packs.
- Existing legacy result filenames remain present.
- New generic files are additive: `workflow.request.json`,
  `workflow.result.json`, and `artifact.manifest.json`.
- `company_brain.bundle.json` remains a deterministic compatibility alias.
- Breaking changes require a new contract version and migration notes.

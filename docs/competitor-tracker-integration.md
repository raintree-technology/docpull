# Competitor-tracker integration contract

Pin DocPull `6.2.0` and validate `intelligence.bundle.v1.json` against
`intelligence-bundle.v1.schema.json` at import time.

## Producer flow

```bash
docpull sync
docpull pack intelligence-bundle .docpull/runs/<run-id> \
  --objective "Track pricing, positioning, product, security, and policy"
```

Python:

```python
from pathlib import Path
from docpull import build_intelligence_bundle

bundle = build_intelligence_bundle(
    Path(".docpull/runs/run_20260716"),
    objective="Track competitor changes",
    market="Developer tools",
)
```

MCP clients call `intelligence_bundle`. Existing integrations may continue to
read `company_brain.bundle.json`; it is an alias containing the v1 fields and a
deprecated compatibility envelope.

## Import requirements

1. Reject an unknown `contract_version`.
2. Recompute the canonical bundle core hash and compare `bundle_hash`.
3. Deduplicate by `bundle_id` and change `idempotency_key`.
4. Store pack ID, run ID, source snapshot ID, document ID, and document version.
5. Treat `observations[*].status == "observation"` as unapproved machine output.
6. Verify each evidence span against the referenced document version before
   presenting it for review.
7. Keep `change_candidates` in a review queue; do not infer approval from source
   authority or confidence.
8. Surface warnings and retain replay configuration for audit/reproduction.

## Ownership split

DocPull owns acquisition, evidence spans, source snapshots, rights/provenance,
budget receipts, hashes, and change candidates. Competitor-tracker owns
scheduling, entity resolution across imported packs, reviewer assignments,
approval/rejection, product-specific severity, alert delivery, and UI.

## Replay and monitoring

`WorkflowRequest.replay` and `ChangeEvent.replay_configuration` deliberately set
`scheduler` to null. A local cron job, CI workflow, desktop agent, or hosted
scheduler can replay the same configuration. Browser and paid-route flags must
remain false unless the operator explicitly enables and budgets those paths.

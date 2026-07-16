# DocPull 6.2: evidence workflows and tracker contracts

DocPull 6.2 formalizes the project as a local-first evidence and acquisition
engine. Brand, product, styleguide, visual, screenshot, and policy lanes now
share a versioned workflow request/result protocol with artifact hashes, budget
receipts, progress, warnings, failures, and replay configuration.

The release adds deterministic `intelligence.bundle.v1` imports and idempotent
`change.event.v1` records for downstream trackers. Machine output is represented
as observations and change candidates with precise evidence spans; approval,
legal conclusions, scheduling, and notifications remain downstream concerns.

All cross-repository contracts ship as Draft 2020-12 JSON Schemas. Existing
packs and `company_brain.bundle.json` remain readable.

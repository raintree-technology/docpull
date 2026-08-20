# Product Marketing Context

*Last updated: 2026-08-20 · Auto-drafted from repository and package evidence*

## Product Overview

**One-liner:** DocPull turns changing public sources into cited, reproducible context for AI agents and retrieval pipelines.

**What it does:** The Python CLI, SDKs, and MCP server acquire declared sources, preserve versions and provenance, report hash-based changes, validate context packs, and export inspectable artifacts. The default workflow runs locally without an account or paid API.

**Product category:** Developer tooling for context engineering, source acquisition, and retrieval pipelines

**Product type:** MIT-licensed open-source CLI, Python package, SDKs, and MCP server

**Business model:** The repository and published packages are open source. No paid DocPull service or validated commercial offer is represented here.

## Target Audience

**Primary users:** Developers and AI platform teams that need sourced, repeatable inputs for agents, RAG systems, evaluations, and documentation workflows.

**Primary use case:** Declare a set of sources, sync them locally, inspect what changed, and export context with enough provenance to reproduce it later.

**Jobs to be done:**

- Rebuild the context behind an answer or artifact.
- Detect drift in documentation, policies, packages, repositories, and other sources.
- Validate citation, freshness, rights, and pack-quality requirements in CI.
- Feed local agents and data workflows without depending on a hosted extraction service.

## Problems and Alternatives

**Core problem:** Agent context is often copied or fetched without a durable record of source versions, changes, citations, or export state.

**Alternatives:** Browser automation, hosted extraction services, one-off scraping scripts, and generic ingestion pipelines may fit different acquisition needs. DocPull is strongest when local execution, versioned evidence, explicit rendering choices, and reproducible artifacts matter.

## Differentiation

- Declared sources and resolved inputs are separate, versioned records.
- Hashes, manifests, run IDs, citations, and lockfiles remain inspectable.
- Raw extraction, agent-ready context, and evaluation-grade evidence have distinct contracts.
- Expensive browser and cloud routes require an explicit choice and budget.
- CLI, Python, TypeScript, MCP, and agent-plugin surfaces share the same local artifacts.

## Objections and Fit

| Question | Answer |
| --- | --- |
| Is this a hosted research product? | No. DocPull is an evidence-acquisition engine and local context tool. |
| Does it automate private dashboards or CAPTCHAs? | No. Complex interactive and stealth workflows are outside the default boundary. |
| Does a successful pack prove the source is true or legally reusable? | No. It proves only the configured acquisition and validation claims. |

**Anti-persona:** Teams seeking a managed competitive-intelligence product, stealth scraping, or fully automated legal and factual approval.

## Customer Language

No verified customer interviews are recorded. Use repository-grounded terms: cited context, source drift, reproducible, local-first, provenance, lockfile, context pack, explicit rendering, and evidence boundary. Avoid: autonomous truth, complete coverage, real-time, guaranteed accuracy, and hosted intelligence.

## Brand Voice

**Tone:** Direct, technical, evidence-minded, and candid about limits

**Style:** Lead with a working command, name the artifact produced, and connect every quality claim to a check or documented boundary.

## Proof Points

- Published `docpull` Python package, currently version 6.5.0 in repository metadata.
- CLI, Python SDK, TypeScript SDK, MCP server, and agent-plugin surfaces.
- Reproducible release build and repository validation suites.
- Public evaluation methods and results under `bench/docs/`.

Package downloads, stars, users, customer outcomes, and commercial traction must be refreshed from dated sources before use.

## Goals

**Primary goal:** Help developers adopt DocPull for repeatable, evidence-aware context workflows.

**Conversion action:** Install `docpull`, sync one declared source, inspect the diff, and export a context pack.

## Messaging Guardrails

- Keep the open-source acquisition engine separate from downstream products.
- Do not present internal fault models, tests, or benchmark fixtures as field accuracy.
- State when browser rendering, credentials, network access, or optional dependencies are required.
- Treat missing evidence as unknown, not proof of absence.

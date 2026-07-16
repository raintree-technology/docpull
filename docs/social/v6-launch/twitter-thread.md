# DocPull v6 Twitter/X Thread

Use one image per tweet. Put the repo or PyPI link in the final tweet or first reply to keep the early tweets cleaner.

## Assets

Generate or refresh the PNGs:

```bash
python3 docs/social/v6-launch/generate_terminal_cards.py
```

Generated images:

- `assets/01-context-drift.png`
- `assets/02-lockfile-workflow.png`
- `assets/03-sync-diff.png`
- `assets/04-v3-pack-contract.png`
- `assets/05-context-ci.png`
- `assets/06-export-agent-context.png`

## Thread

### 1/8

The first AI agent bug I keep seeing is not hallucination.

It's context drift.

The model may be fine. The docs it sees are stale, uncited, copied from Slack, or embedded months ago.

DocPull v6 turns agent context into something you can lock, diff, and test.

Attach: `assets/01-context-drift.png`

Alt text: Terminal-style graphic showing stale agent context files with no citations, no diff, no CI gate, and no provenance.

### 2/8

You already solve this for code.

`package-lock.json` tells you exactly what your app depends on.

Agents need the same thing for docs, specs, repos, packages, feeds, datasets, and standards.

That's the category: context dependencies.

Attach: `assets/02-lockfile-workflow.png`

Alt text: Terminal-style graphic showing `docpull init`, `docpull add stripe react openai`, and `docpull install` writing `.docpull/context.lock.json`.

### 3/8

The core workflow:

```bash
docpull init agent-context
docpull add stripe react openai
docpull install
docpull sync
docpull diff
docpull ci --prepare
docpull export context-pack --target codex
```

This makes source changes reviewable before they become model behavior.

Attach: none, or reuse `assets/02-lockfile-workflow.png` if you want every tweet to have media.

### 4/8

v6 is built around one mechanism: the v3 context-pack contract.

Every ingestion lane normalizes into the same artifact shape: web, local docs, OpenAPI, feeds, papers, repos, packages, standards, datasets, transcripts, wiki.

One contract. Many sources.

Attach: `assets/04-v3-pack-contract.png`

Alt text: Terminal-style graphic showing v3 pack validation across raw artifacts, agent sidecars including coverage and audit files, and eval-grade rights, provenance, basis, and pack-card artifacts.

### 5/8

That contract matters because agents don't just need text.

They need to know:

- where it came from
- when it was fetched
- what changed
- what can be cited
- whether it's ready for evals or CI

Markdown is the medium. The artifact is the product.

Attach: none, or `assets/04-v3-pack-contract.png`

### 6/8

Once context is an artifact, CI can enforce it.

`docpull ci --prepare` can fail on stale runs, weak citation coverage, missing eval-grade sidecars, lockfile drift, or scores below your thresholds.

Bad context should fail before it reaches the agent.

Attach: `assets/05-context-ci.png`

Alt text: Terminal-style graphic showing `docpull ci --prepare` with passing lockfile, pack score, audit score, coverage confidence, and eval artifact gates, plus a failing citation coverage gate and a rights status warning.

### 7/8

DocPull isn't a hosted browser farm or a black-box research API.

It's the local artifact layer between selected sources and agent behavior:

sources -> cited pack -> validation -> export -> agents/RAG/CI

Browser rendering and cloud routes stay explicit.

Attach: `assets/03-sync-diff.png`

Alt text: Terminal-style graphic showing `docpull sync` and `docpull diff` detecting added, removed, changed, API, and pricing-related source updates.

### 8/8

Try it:

```bash
pip install docpull
docpull init my-agent-context
docpull add stripe react openai
docpull sync
docpull ci --prepare
```

https://github.com/raintree-technology/docpull

If your agent depends on external sources, make them dependencies.

Attach: `assets/06-export-agent-context.png`

Alt text: Terminal-style graphic showing project context exported with `docpull export context-pack` to Cursor, Codex, and OpenAI targets, with the export recorded in `.docpull/context.lock.json`.

## Shorter 5-Tweet Version

### 1/5

The first AI agent bug I keep seeing is not hallucination.

It's context drift.

DocPull v6 turns agent context into a dependency workflow: declare sources, lock them, sync them, diff them, and check them in CI.

Attach: `assets/01-context-drift.png`

### 2/5

The workflow:

```bash
docpull init agent-context
docpull add stripe react openai
docpull install
docpull sync
docpull diff
```

This gives you `docpull.yaml`, `.docpull/context.lock.json`, run artifacts, and source diffs your team can review before context reaches an agent.

Attach: `assets/02-lockfile-workflow.png`

### 3/5

v6 centers on one mechanism: the v3 context-pack contract.

Web docs, OpenAPI specs, feeds, repos, packages, standards, datasets, transcripts, and local files all normalize into the same artifact shape.

Raw -> agent-ready -> eval-grade.

Attach: `assets/04-v3-pack-contract.png`

### 4/5

Then CI can enforce context quality:

- lockfile matches project
- citations are present
- pack/audit scores pass
- rights/provenance sidecars exist
- stale or weak context fails before it reaches the agent

```bash
docpull ci --prepare
```

Attach: `assets/05-context-ci.png`

### 5/5

DocPull is the local artifact layer for agent context:

sources -> cited pack -> validation -> export -> agents/RAG/CI

```bash
pip install docpull
```

https://github.com/raintree-technology/docpull

If your agent depends on external sources, make them dependencies.

Attach: `assets/06-export-agent-context.png`

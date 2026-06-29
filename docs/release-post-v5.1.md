# DocPull v5.1: Refreshable Context Packs For AI Agents

DocPull now supports project-based, incremental evidence pipelines for AI
agents.

Define source sets once, sync them over time, diff what changed, and export
fresh context packs for Cursor, Claude, Codex, OpenAI, LlamaIndex, and
LangChain.

```bash
pip install docpull

docpull init stripe-docs
docpull add https://docs.stripe.com
docpull sync
docpull diff
docpull export context-pack --target cursor
```

The original `docpull URL ...` flow still works. v5.1 adds the persistent
lifecycle around it:

- `docpull.yaml` for source definitions
- `.docpull/runs/<run_id>/` for durable run artifacts
- `index.sqlite` for local source/run/document/chunk indexing
- deterministic local diffs for added, removed, changed, pricing, and likely
  API behavior changes
- cited context packs for coding agents and RAG pipelines
- eval-set JSONL generation from changed or latest documents

The useful claim is narrow and concrete:

> DocPull turns public docs and web sources into refreshable, cited,
> agent-ready context packs.

This is the foundation for keeping agent context fresh without a hosted
scheduler, hidden paid calls, or a proprietary web index.

Demo asset:

`docs/launch-assets/docpull-project-diff-demo.png`

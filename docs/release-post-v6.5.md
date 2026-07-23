# DocPull 6.5: provenance controls and a TypeScript SDK

DocPull 6.5 adds tamper-evident context packs, prompt-injection screening,
source opt-out handling, WARC export, and explicit provenance records. Pack
authors can create digest manifests, optionally sign them with Ed25519, and
verify both content and signature state before an agent consumes the pack.

The new `@raintree-technology/docpull-sdk` package reads DocPull packs from Node or Bun and invokes
the local CLI without a shell. Python integrations for LangChain and
LlamaIndex expose the same cited local artifacts without changing DocPull's
browser-free and budget-guarded defaults.

Benchmark reports now include token accounting and local baseline adapters.
The existing CLI, Python SDK, MCP, artifact contracts, intelligence bundles,
and company-brain workflows remain compatible. No migration is required for
existing packs.

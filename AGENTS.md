# DocPull Agent Instructions

DocPull is the open-source context dependency manager for AI agents. Its core
contract is local-first, browser-free by default, reproducible, cited, and
budget guarded.

- Preserve CLI, Python SDK, MCP, and artifact-contract compatibility.
- Do not silently enable browser or paid/cloud rendering. Those paths require
  explicit user configuration and budgets.
- Respect robots, source rights, provenance, and existing cache/lock semantics.
- Never store credentials or fetched private content in fixtures or reports.
- Use uv for Python work and Bun 1.3.11/Node 24 for the web and MCP packages.
- Run the focused pytest suite while iterating and `bun run validate:full` from
  the root for repository-wide changes.

# DocPull v6 Directory Follow-Ups

## Manual Verification

- Glama MCP: live listing and score badge verified; `punkpeye/awesome-mcp-servers#9276` includes the badge and passes `check-submission`.
- allMCPservers: complete the Google reCAPTCHA/manual verification for the already-filled official MCP form.
- MCP.Directory: watch email/listing status because the form cleared after submit but did not show a reliable success banner.
- HN: add the posted Show HN item URL to `docs/v6-directory-tracker.csv`.

## Site Readiness

- Deploy the restored web routes for `/privacy`, `/terms`, `/pricing`, and `/llms.txt`; they were implemented locally on 2026-07-06 but still need live 200 checks.
- Add launch screenshots and a 45-60 second demo video before Product Hunt, DevHunt, Fazier, Microlaunch, or Uneed.
- Product Hunt: schedule only after the launch assets are ready and the founder account is warmed up; do not ask for upvotes, ask for feedback.

## Package / Registry Work

- Cline MCP Marketplace: run a real Cline install test using only `README.md` and/or `llms-install.md`; use the ready `docs/launch-assets/logo-square-light-400.png` asset.
- Cursor/Windsurf Directory: add Open Plugins metadata (`.mcp.json`) to the GitHub repo, then submit through `https://cursor.directory/plugins/new`.
- Stacklok ToolHive Catalog: add an OCI image or remote Streamable HTTP MCP server before submitting; current catalog accepts containers/remotes, not PyPI stdio packages.
- Smithery: add an MCPB bundle or a remote Streamable HTTP MCP server before submitting.

## Account-Gated Launch Surfaces

- AgentLocker: create/verify an account, then submit through `/agent/submit`.
- OpenTools: sign in and look for an authenticated registry submission path; no public endpoint was found.
- DevHunt, Fazier, Microlaunch, Uneed: retry after policy/pricing pages and launch assets are deployed.

## Content / Community

- Reddit: post a technical r/mcp/r/SideProject-style walkthrough with install command, tool list, and authorship disclosure.
- DEV/Hashnode: publish a tutorial, not a raw launch announcement.
- Awesome-RAG / Awesome-LLMOps / awesome-harness-engineering: submit after a concrete RAG/context-dependency tutorial exists.

## Watchlist

- 2026-07-07 daily monitor: open public PRs/issues remain maintainer-waiting with no requested changes. `punkpeye/awesome-mcp-servers#9276`, `lorien/awesome-web-scraping#262`, and `toolsdk-ai/toolsdk-mcp-registry#381` have passing active checks; `lorien#262` still exposes an older failed run in the rollup. Official MCP Registry, Glama, and MCPRepository listings remain live. mcp.so, mcpservers.org, MCP.Directory, MCP Server Hub, and AiAgents.Directory did not show confirmed live DocPull listing pages in lightweight checks. appcypher still has a one-commit-ahead branch and no PR.
- 2026-07-06 daily monitor: open PRs/issues remain maintainer-waiting with no requested changes; `punkpeye/awesome-mcp-servers#9276`, `lorien/awesome-web-scraping#262`, and `toolsdk-ai/toolsdk-mcp-registry#381` have passing required checks. MCPRepository is now live with GitHub/PyPI links. mcp.so, mcpservers.org, and PulseMCP did not show new public listing pages in lightweight checks.
- MCP Market: email request sent; optionally retry the free queue manually, but do not pay for review without approval.
- MCPCentral: no supported write endpoint found; recheck after Official MCP Registry propagation.
- AgenticSkills: public submit API returned HTTP 503 because the review queue is not configured; retry later.
- MCPera and GPTMCP: both appear to be demo-only forms that do not send submissions.

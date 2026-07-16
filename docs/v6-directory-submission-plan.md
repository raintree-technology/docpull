# DocPull v6 Directory Submission Plan

Verified on 2026-07-04. This plan turns the existing launch kit and visibility
research into an execution order for DocPull v6.

## Readiness

Ready:

- Public documentation: https://github.com/raintree-technology/docpull
- GitHub: https://github.com/raintree-technology/docpull
- PyPI: https://pypi.org/project/docpull/
- Version: `6.0.0`
- GitHub topics are filled with relevant Python, MCP, RAG, web extraction, and
  agent-context terms.
- PyPI keywords and project URLs include docs, repository, comparison guide,
  MCP plugin, changelog, releases, and download stats.
- Launch assets exist under `docs/launch-assets`.
- v6 X thread assets exist under `docs/social/v6-launch`.
- GitHub README has package links, MCP install instructions, and clear install
  CTAs.

Canonical owned surfaces:

- GitHub repository and release pages.
- PyPI package page.
- Official MCP Registry metadata.
- No standalone DocPull website is operated or required.

Still recommended before Product Hunt:

- Record a 45-60 second terminal demo from `pip install docpull` to `docpull
  sync`, `docpull diff`, and `docpull export context-pack --target cursor`.
- Use terminal, GitHub, and package-install screenshots for launch materials.
- Use the GitHub repository URL for Product Hunt and other directory listings.

## Positioning

Primary category:

- Context dependencies for AI agents.

Short tagline:

- Keep AI agents synced with changing public docs.

Product Hunt tagline:

- Turn public web sources into agent-ready context packs.

Long description:

DocPull is a local-first dependency manager for AI context. Define the public
docs and web sources an agent depends on, sync them into cited context packs,
diff what changed, and export reproducible context for Cursor, Claude, OpenAI,
LlamaIndex, LangChain, MCP clients, and RAG pipelines.

DocPull is browser-free by default and has explicit budget controls for any
paid-capable cloud route. It fits teams building coding agents,
RAG systems, MCP tools, and docs-aware automation where stale or uncited context
should be visible before it changes model behavior.

Third-party API stance:

- Show the adapter ecosystem instead of flattening DocPull into "web scraping."
  Public v6 adapters include OpenAPI, feeds, repos, packages, papers,
  standards, datasets, transcripts, wiki pages, parsers, render runtimes, MCP,
  agent exports, RAG exports, automation exports, and warehouse/table exports.
- Parallel Web, Tavily, and Exa provider adapter code exists in the repository.
  Label it as experimental/internal provider code unless the release contract
  promotes the exact CLI, SDK, MCP, or package-extra surface.
- Pitch DocPull as the artifact layer that turns selected URLs, files, specs,
  packages, feeds, datasets, provider records, or research outputs into local
  packs with lockfiles, citations, validation, diffs, and exports.

## Submission Order

### Batch 0: Metadata and owned surfaces

Do this first because every directory links back here.

1. Confirm the GitHub repository and latest release are public.
2. Confirm PyPI shows the current supported version.
3. Confirm the official MCP Registry metadata is published.
4. Pin the v6 X thread after posting.

### Batch 1: Developer and MCP surfaces

These are highest fit for DocPull.

1. Official MCP Registry: publish server metadata.
2. Glama MCP: submit the GitHub repository and inspect the generated server
   page.
3. Smithery: publish if the local stdio install flow fits their current server
   model.
4. mcp.so: submit through the GitHub issue flow with install command and stdio
   config.
5. Cline MCP Marketplace: submit only after a Cline setup test and 400x400 PNG
   logo are ready.
6. DevHunt: submit as Developer Tools / Open Source / AI / CLI / Python / MCP.
7. PyCoder's Weekly: submit the website or GitHub release link.
8. Python Bytes: send a short email pitch with one terminal example.

### Batch 2: Community launch

Use a technical story, not generic launch copy.

1. r/mcp showcase with install command and MCP tools.
2. DEV Community or Hashnode tutorial: "Context dependencies for AI agents with
   DocPull v6."
3. Hacker News Show HN using `docs/v6-hacker-news-plan.md`; the founder should
   rewrite the first comment in their own voice before posting.
4. r/Python or r/webscraping only after the tutorial is live.

### Batch 3: Product launch platforms

Use these after Batch 1 has seeded credibility.

1. Product Hunt.
2. DevHunt, if not already submitted in Batch 1.
3. Uneed.
4. Microlaunch.
5. Fazier.
6. BetaList only if we want waitlist/startup exposure; it is weaker for pure
   OSS devtool usage.

Earliest strong Product Hunt date from today: Tuesday, 2026-07-28. Product Hunt
allows scheduling up to one month ahead, so schedule during prep rather than
submitting cold on launch morning.

### Batch 4: GitHub awesome lists and Python directories

Open focused PRs only where DocPull clearly fits.

1. awesome-mcp-servers.
2. awesome-mcp-devtools.
3. awesome-web-scraping. Use public web extraction / Python CLI framing only;
   its contribution rules restrict AI-agent and MCP automation submissions.
4. best-of-python-dev.
5. best-of-web-python.
6. awesome-copilot, only if submitting a real Copilot plugin/skill/tool entry
   rather than a generic product listing.
7. awesome-python, as a long-shot after v6 traction is visible.
8. awesome-agents, awesome-ai-devtools, Awesome-LLMOps, Awesome-RAG, and
   awesome-harness-engineering only with a precise category fit and proof link.

Detailed GitHub repo targets, suggested categories, and skip/defer notes live in
[`docs/v6-github-list-targets.md`](v6-github-list-targets.md).

### Batch 5: Long-tail SEO directories

Do these after the core developer surfaces. Avoid paid or reciprocal-badge
submissions unless there is a clear category fit.

1. AlternativeTo.
2. SaaSHub.
3. StackShare.
4. SourceForge.
5. Future Tools.
6. Futurepedia.
7. There's An AI For That only if paid placement is acceptable.

## Product Hunt Prep Calendar

Assuming a launch on Tuesday, 2026-07-28:

- 2026-07-07: create Product Hunt draft and Upcoming page.
- 2026-07-08 to 2026-07-20: engage from the maker account with real comments on
  relevant developer, AI agent, and open-source launches.
- 2026-07-14: finalize gallery images, demo video, 60-character tagline,
  500-character description, maker comment, and first replies.
- 2026-07-21: send warm heads-up to existing users, friends, and communities.
  Ask for feedback, not upvotes.
- 2026-07-27: final route check in incognito: website, GitHub, PyPI, docs,
  pricing, privacy, terms, demo media, and signup/install path.
- 2026-07-28: launch at the selected Product Hunt time and keep the maker
  account available for comments.

## Product Hunt Assets

Name:

- docpull

Tagline:

- Turn public web sources into agent-ready context packs.

Description, under 500 characters:

DocPull is a Python CLI, SDK, and MCP server for turning public docs and web
sources into cited, refreshable context packs. Declare sources, sync them,
diff what changed, validate context in CI, and export reproducible context for
Cursor, Claude, OpenAI, LlamaIndex, LangChain, MCP clients, and RAG.

Launch tags:

- Engineering & Development
- AI Agents
- LLMs

Maker comment angle:

DocPull v6 is about making agent context behave more like code dependencies:
declared, locked, synced, diffed, and tested before it changes model behavior.
The sharp boundary is intentional: browser-free by default, explicit rendering
only when needed, and `--budget 0` for no paid-capable routes.

## Measurement

Track weekly:

- Directory submissions sent.
- Listings live.
- Backlinks verified.
- GitHub stars.
- PyPI downloads.
- Website referrals.
- Mentions in ChatGPT, Claude, Perplexity, and Google AI Overviews for
  "context dependencies", "agent context packs", "Context CI", and
  "context pack validation".

## Verification Sources

- Product Hunt launch guide: https://www.producthunt.com/launch/preparing-for-launch
- Product Hunt preparation guide: https://www.producthunt.com/launch/before-launch
- MCP Registry docs: https://modelcontextprotocol.io/registry/about
- DevHunt: https://devhunt.org/
- Smithery publishing docs: https://smithery.ai/docs/build/publish
- PyCoder's Weekly submissions: https://pycoders.com/submissions
- Python Bytes contact: https://pythonbytes.fm/home/contact
- GitHub list target research: docs/v6-github-list-targets.md

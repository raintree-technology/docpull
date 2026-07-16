# DocPull v6 GitHub List Targets

Verified on 2026-07-04 from GitHub repository pages, raw contribution files,
and GitHub API metadata. Use this as the PR target list for the GitHub
"awesome list" portion of the directory launch.

## Highest-Fit PR Order

1. `punkpeye/awesome-mcp-servers`
   - URL: https://github.com/punkpeye/awesome-mcp-servers
   - Fit: MCP server directory with relevant categories for Developer Tools,
     Knowledge & Memory, Search & Data Extraction, and end-to-end RAG.
   - Submission angle: local-first MCP server for fetching, indexing,
     diffing, and exporting cited public docs as agent context packs.
   - Next step: submit after the official MCP Registry or Glama listing is
     live so the PR has external validation.

2. `punkpeye/awesome-mcp-devtools`
   - URL: https://github.com/punkpeye/awesome-mcp-devtools
   - Fit: MCP developer tools, SDKs, libraries, utilities, and resources.
   - Suggested category: `Utilities` / `Development Tools`.
   - Submission angle: Python CLI/SDK/MCP utility for producing reproducible
     context packs from public docs and web sources.

3. `lorien/awesome-web-scraping`
   - URL: https://github.com/lorien/awesome-web-scraping
   - Fit: Python web scraping and command-line web extraction lists.
   - Suggested files: `python.md` and/or `cli.md`.
   - Submission angle: Python CLI/SDK for public web-to-Markdown extraction
     with citations and refreshable local packs.
   - Caveat: the contribution rules restrict AI-agent and MCP automation
     content, so do not lead with agent/MCP positioning in this PR.

4. `ml-tooling/best-of-python-dev`
   - URL: https://github.com/ml-tooling/best-of-python-dev
   - Fit: Python developer tools, updated weekly, accepts issues or PRs to
     `projects.yaml`.
   - Suggested category: `documentation`.
   - Submission angle: developer documentation/context dependency tooling.
   - Draft YAML:

     ```yaml
     - name: docpull
       github_id: raintree-technology/docpull
       pypi_id: docpull
       category: documentation
     ```

5. `ml-tooling/best-of-web-python`
   - URL: https://github.com/ml-tooling/best-of-web-python
   - Fit: Python web libraries, updated weekly, accepts issues or PRs to
     `projects.yaml`.
   - Suggested category: `html-processing` first, `url-utils` only if the
     maintainer prefers the URL discovery angle.
   - Submission angle: public web and docs extraction to structured Markdown
     / local context packs.

6. `h4ckf0r0day/awesome-ai-web-scraping`
   - URL: https://github.com/h4ckf0r0day/awesome-ai-web-scraping
   - Fit: explicit AI web scraping list with a dedicated `MCP Servers for
     Scraping` section. The README scope includes LLM-friendly crawlers, MCP
     servers, RAG pipelines, and agents.
   - Submission angle: local-first Python CLI/SDK/MCP server for turning public
     static and server-rendered web sources into Markdown, NDJSON, SQLite, and
     cited context packs.
   - Caveat: keep the entry concise and in the MCP scraping section; this is
     not a generic Python scraper listing.

7. `TensorBlock/awesome-mcp-servers`
   - URL: https://github.com/TensorBlock/awesome-mcp-servers
   - Fit: active MCP index with a hosted searchable profile site, issue forms,
     and direct category markdown PRs.
   - Suggested category: `Browser Automation & Web Scraping`.
   - Submission angle: MCP server for fetching, caching, searching, validating,
     and exporting public static/server-rendered web sources as cited local
     context packs.
   - Caveat: use a custom fork name because `zacharyr0th/awesome-mcp-servers`
     is already the fork used for the punkpeye PR.

8. `github/awesome-copilot`
   - URL: https://github.com/github/awesome-copilot
   - Fit: tools section covers MCP servers and developer tooling; repo also
     accepts agents, skills, hooks, workflows, and plugins.
   - Submission angle: only submit a concrete DocPull Copilot plugin, skill,
     or MCP tool entry. Do not submit a generic repo listing.
   - Next step: package the DocPull workflow as a Copilot-ready skill/plugin
     before opening a PR.

## Secondary Targets

9. `vinta/awesome-python`
   - URL: https://github.com/vinta/awesome-python
   - Fit: huge Python list with a Web Scraping category.
   - Caveat: strict quality bar. Requires Python-first, active, stable,
     documented, unique, and established projects. Treat as a long-shot until
     v6 has visible adoption.

10. `kyrolabs/awesome-agents`
   - URL: https://github.com/kyrolabs/awesome-agents
   - Fit: has Knowledge Management and Software Development categories.
   - Caveat: contribution rules focus on agentic frameworks, so DocPull may be
     considered adjacent rather than core. Submit only with a clear agent
     context-dependency framing and proof of traction.

11. `jamesmurdza/awesome-ai-devtools`
   - URL: https://github.com/jamesmurdza/awesome-ai-devtools
   - Fit: CLI utilities, configuration/context management, documentation
     generation, and agent infrastructure sections.
   - Caveat: PR template says entries must be tools that use AI. DocPull is
     mostly AI-supporting infrastructure, so this is medium-risk unless the
     pitch leads with Context CI, agent exports, or MCP workflows.

12. `tensorchord/Awesome-LLMOps`
    - URL: https://github.com/tensorchord/Awesome-LLMOps
    - Fit: LLMOps list with Search, Data Management, Observability, and
      LLMOps categories.
    - Submission angle: context dependency management for RAG and agent
      pipelines, especially sync/diff/validate/export.

13. `Danielskry/Awesome-RAG`
    - URL: https://github.com/Danielskry/Awesome-RAG
    - Fit: RAG tools and resources.
    - Submission angle: refreshable cited source packs for RAG ingestion.
    - Next step: publish a short RAG tutorial or example pack first; a bare
      tool listing is weaker.

14. `ai-boost/awesome-harness-engineering`
    - URL: https://github.com/ai-boost/awesome-harness-engineering
    - Fit: context delivery, MCP, verification/CI, and agent harness design.
    - Submission angle: DocPull as a context delivery and verification
      component for agent harnesses.
    - Caveat: this list is article/resource-heavy. A technical post about
      context dependencies may be a better submission than the repo alone.

## Skip or Watch

- `e2b-dev/awesome-ai-agents`: large but mostly autonomous agents; DocPull is
  supporting infrastructure, and the repo has less recent activity than better
  targets.
- `Shubhamsaboo/awesome-llm-apps`: very high reach, but it focuses on runnable
  AI Agent and RAG apps. Consider only if DocPull publishes a runnable demo app.
- `hyp1231/awesome-llm-powered-agent`: stronger for papers/blogs/repos about
  agents than for developer infrastructure. Defer unless there is a research or
  technical article.
- Fresh `awesome-* 2026` scraper/agent repos with heavy self-promotion: skip
  unless they show stable maintenance and a non-spammy contribution process.

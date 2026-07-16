# DocPull v6 Sales And PR Automation

This is the systematic operating plan for selling DocPull v6 and turning the
GitHub list work into repeatable PRs from the `zacharyr0th` account.

## Account Baseline

Local state checked on 2026-07-04:

- Git author: `Zachary Roth <100426704+zacharyr0th@users.noreply.github.com>`
- `gh` active account: `zacharyr0th`
- Git protocol: SSH
- Repo remote: `git@github.com:raintree-technology/docpull.git`

This means PR automation can safely use `gh` and forked branches under
`zacharyr0th`, as long as each PR is reviewed before pushing.

## Sales Motion

DocPull should not be sold as "another scraper." The strongest shelf is:

> Context dependencies for AI agents.

Use that as the main story on agent, MCP, RAG, and devtool surfaces. Use
"browser-free public web extraction" only where the audience is explicitly
web-scraping or Python web tooling.

Primary pitch:

> DocPull lets teams declare the public docs and web sources an agent depends
> on, sync them into cited context packs, diff what changed, and fail CI when
> the evidence is stale.

The buying argument:

- Agents are only as reliable as the context loaded into them.
- Vendor docs, API behavior, pricing pages, changelogs, standards, packages,
  and repos change constantly.
- Raw URLs and ad hoc browser scraping are not a dependency system.
- DocPull makes context behave like code dependencies: declared, locked,
  synced, diffed, validated, and exported.

## Audience Segments

1. Agent/MCP builders
   - Pain: agents need current docs/tools without relying on raw browser output.
   - Lead with: MCP server, context packs, `docpull ci`, local-first workflow.
   - CTA: install `docpull[mcp]`, add server to MCP client, run a docs sync.

2. RAG/search engineers
   - Pain: ingestion quality and citations are weak links in RAG systems.
   - Lead with: Markdown/NDJSON/chunks/citations, provenance, refresh/diff.
   - CTA: build a pack from vendor docs and export JSONL.

3. Python/web extraction developers
   - Pain: need clean public web extraction without browser automation.
   - Lead with: static/server-rendered HTML, async fetching, Markdown/SQLite,
     safety boundary.
   - CTA: `pip install docpull` and fetch one real docs site.

4. Coding-agent teams
   - Pain: generated code breaks because docs in context are stale or uncited.
   - Lead with: context dependencies, lockfile, Context CI, and agent-ready
     exports.
   - CTA: add DocPull to CI as a context gate.

## Surface-Specific Positioning

| Surface | Lead with | Avoid |
|---|---|---|
| MCP registries | MCP server for cited context dependencies | Generic scraper copy |
| GitHub MCP lists | Local MCP server, install command, tool surface | Long marketing descriptions |
| Python lists | Python package, CLI/SDK, clean web extraction | Agent hype |
| Web-scraping lists | Public static/server-rendered extraction | MCP/agent automation framing |
| RAG/LLMOps lists | Refreshable cited ingestion packs | "Crawler" as the whole story |
| Product Hunt | Agent context dependency manager | Dense implementation details |
| HN/Reddit | Technical boundary and examples | Launch announcement tone |

## PR Automation Policy

Automate the mechanics, not the judgment.

Do:

- Generate a per-repo branch from a small recipe.
- Keep one upstream PR per repository.
- Follow the target repo's contribution format exactly.
- Use one-sentence entries for awesome lists.
- Create PR bodies that explain fit and disclose maintainer-facing caveats.
- Log status in `docs/v6-directory-tracker.csv`.

Do not:

- Bulk-open PRs across every repo in one shot.
- Submit to weak-fit lists just because automation makes it easy.
- Lead with AI/MCP on `lorien/awesome-web-scraping`; their rules restrict
  agent/MCP automation content.
- Open `github/awesome-copilot` until there is a concrete Copilot skill,
  plugin, or MCP tool listing to submit.

## Automation Workflow

Dry run all recipes:

```bash
python scripts/github_list_prs.py --recipes docs/v6-github-pr-recipes.json --dry-run
```

Prepare one local branch and commit without pushing:

```bash
python scripts/github_list_prs.py \
  --recipes docs/v6-github-pr-recipes.json \
  --id awesome-mcp-devtools \
  --prepare
```

Push and create a PR after reviewing the local diff:

```bash
python scripts/github_list_prs.py \
  --recipes docs/v6-github-pr-recipes.json \
  --id awesome-mcp-devtools \
  --prepare \
  --push \
  --create-pr
```

Recommended launch order:

1. Official MCP Registry / Glama / Smithery listings.
2. `punkpeye/awesome-mcp-servers`.
3. `punkpeye/awesome-mcp-devtools`.
4. `ml-tooling/best-of-python-dev`.
5. `ml-tooling/best-of-web-python`.
6. `lorien/awesome-web-scraping`, using web extraction framing.
7. `h4ckf0r0day/awesome-ai-web-scraping`, using MCP scraping-server framing.
8. `TensorBlock/awesome-mcp-servers`, using Browser Automation & Web Scraping
   category framing.
9. Secondary RAG/LLMOps/agent-harness lists only after a tutorial exists.

## PR Review Checklist

Before pushing each PR:

- The entry is in the correct section.
- The line is concise and factual.
- The link points to `https://github.com/raintree-technology/docpull`.
- The pitch matches that repository's audience.
- The PR title is specific and not promotional.
- The PR body mentions why DocPull belongs in that exact section.
- There are no unrelated file changes from the cloned target repo.

## Tracking

Update `docs/v6-directory-tracker.csv` after every submission:

- `Status`: `PR Open`, `Merged`, `Rejected`, `Needs Changes`, or `Deferred`
- `Submission Date`: ISO date
- `Live URL`: PR URL first, merged listing URL after merge
- `Notes`: maintainer feedback or next action

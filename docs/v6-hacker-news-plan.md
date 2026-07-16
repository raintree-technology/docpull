# DocPull Hacker News Plan

Verified on 2026-07-04.

## Official Constraints

- Use Show HN only because DocPull is something people can run and try:
  https://news.ycombinator.com/showhn.html
- Submit the original source or primary try-it page. For DocPull, the GitHub
  repo is the safest target because it has install commands, source, examples,
  and no signup wall: https://github.com/raintree-technology/docpull
- Title must begin with `Show HN`; keep it neutral, no hype, no exclamation
  points: https://news.ycombinator.com/newsguidelines.html
- Do not ask anyone for upvotes or comments:
  https://news.ycombinator.com/showhn.html
- Submit the link first, then add context as a normal comment:
  https://news.ycombinator.com/newsfaq.html
- HN says not to post generated or AI-edited text. Treat the title/comment below
  as an internal draft only; the founder should rewrite it in their own words
  before posting.

## Current Submit Status

The public submit page currently requires login:
https://news.ycombinator.com/submit

Status: ready for a logged-in human post. Do not use automation to submit the
comment.

## Recommended Link

```text
https://github.com/raintree-technology/docpull
```

Why: HN Show HN guidelines prefer something people can try. The repo is
installable, inspectable, and avoids a landing-page-only first impression.

## Title Draft

```text
Show HN: DocPull - sync public docs into agent-ready context packs
```

Alternates:

```text
Show HN: DocPull - local web-to-Markdown packs for coding agents
Show HN: DocPull - context dependencies for AI agents
Show HN: DocPull - browser-free public docs extraction for agents
```

Use the first title unless the README has shifted to a different primary
positioning. It is specific, technical, and not over-claiming.

## First Comment Draft

Rewrite this in the founder's own voice before posting.

```text
Hi HN, I built DocPull because coding agents and RAG pipelines often depend on public docs, changelogs, standards, and API references that change underneath them.

The tool is a Python CLI/SDK/MCP server that fetches public static and server-rendered web sources, extracts the useful content, and writes local Markdown, NDJSON, SQLite, or archive outputs with source metadata. The goal is to make web context behave more like a dependency: declared, synced, diffed, cached, validated, and easy to inspect.

It is browser-free by default. For dynamic pages, the boundary is explicit: either use the local rendering path or use a browser automation tool. I wanted the default path to stay auditable and cheap instead of silently turning every docs crawl into a hosted scraping job.

Try it:

pip install docpull
docpull https://docs.python.org/3/library/asyncio.html --single
docpull https://www.python.org/blogs/ --max-pages 25 -o ./python-news

For MCP:

pip install 'docpull[mcp]'
docpull mcp

I would especially like feedback on the boundary between browser-free extraction and full browser automation. What sources would you expect a tool like this to handle, and where should it intentionally stop?
```

## Posting Checklist

- Confirm the repo README has the simplest install command in the first screen.
- Confirm PyPI download count is current if mentioning it in comments.
- Submit the GitHub link with the selected `Show HN:` title.
- Immediately add the rewritten first comment as a normal comment.
- Be available for the first 2-3 hours to answer technical questions.
- Do not message friends, users, or communities asking for upvotes or comments.
- Share later as "we posted, feedback welcome" only where the audience would
  naturally care, without asking for votes.

## Likely Questions To Prepare For

- Why not use Firecrawl, Jina Reader, Crawlee, agent-browser, or Scrapy?
- What does it do when the useful content is behind JavaScript?
- What anti-abuse and security boundaries exist for agent-selected URLs?
- How does it preserve citations/provenance?
- How does `--budget 0` prevent paid-capable routes?
- What is the MCP tool surface?
- What is the smallest real workflow where DocPull beats a one-off script?

# Project Agent Instructions

## docpull MCP

This repo ships the `docpull mcp` stdio server for agent clients. Claude Code, Cursor, and Codex should all use the same server command:

```bash
docpull mcp
```

Install the MCP extra before relying on the server:

```bash
pip install 'docpull[mcp]'
docpull mcp --help
```

Codex can add the server to the shared CLI/IDE MCP config:

```bash
codex mcp add docpull -- docpull mcp
```

In trusted projects, Codex also supports project-scoped `.codex/config.toml`:

```toml
[mcp_servers.docpull]
command = "docpull"
args = ["mcp"]
```

For a repo-scoped reusable skill, Codex discovers skills in `.agents/skills` from the current directory up to the repo root. The equivalent skill path is `.agents/skills/docpull-research/SKILL.md`.

## docpull Research Behavior

Use docpull MCP tools when the user asks about a specific library, framework, SDK, API surface, docs-backed tool ecosystem, version-sensitive behavior, or pasted documentation URL.

1. Check cached sources with `mcp__docpull__list_indexed`.
2. If the requested library is cached, search it with `mcp__docpull__grep_docs`.
3. Use `mcp__docpull__read_doc` for line-level follow-up context.
4. If the library is not cached:
   - use `mcp__docpull__ensure_docs` for a built-in alias
   - use `mcp__docpull__fetch_url` for one pasted page
   - otherwise ask once for the docs URL
5. Answer with attribution to the fetched source.

For Vercel skills, `skills.sh`, `npx skills`, agent skill installation, or `SKILL.md` questions, treat the docs as version-sensitive. Search cached docs first for exact commands and flags such as `skills add`, `--agent`, `--skill`, `--copy`, `--yes`, `skills use`, `skills list`, `skills find`, `skills update`, and `skills remove`. If no cached source exists, use a pasted skills.sh URL with `mcp__docpull__fetch_url`; otherwise prefer `https://www.skills.sh/docs` for quick one-page answers and the official Vercel Labs Skills README for CLI option details.

Do not use docpull for general programming explanations, the user's own codebase, or stable standard-library APIs.

Built-in aliases include `react`, `nextjs`, `tailwindcss`, `vite`, `hono`, `fastapi`, `express`, `anthropic`, `openai`, `langchain`, `supabase`, `drizzle`, and `prisma`.

## Skills Installer

When installing skills with `npx skills add <package>`, always pass these flags:

```bash
npx -y skills add <package> --skill '*' --agent codex --copy --yes
```

After install, the CLI still creates `.agents/` and `skills-lock.json` at project root. Delete both to keep the layout flat:

```bash
rm -rf .agents skills-lock.json
```

Keep installer artifacts ignored: `.agents/`, `.crush/`, `.goose/`, `.pi/`, top-level `skills/`, and `skills-lock.json`. If this repo intentionally checks in Codex repo skills or a Codex plugin marketplace, allow only the specific `.agents/skills/...` or `.agents/plugins/...` files needed for that setup.

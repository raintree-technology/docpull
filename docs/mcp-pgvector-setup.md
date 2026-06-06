# pgvector MCP Setup

Most users should use the Python MCP server:

```bash
pip install 'docpull[mcp]'
docpull mcp
```

That path is local, requires no database, and powers the Claude plugin and
standard `docpull` MCP setup.

The separate TypeScript server in `mcp/` is for users who specifically want
persistent semantic search backed by PostgreSQL, pgvector, and OpenAI
embeddings. It is a different server from `docpull mcp`.

## When to Use It

Use the pgvector server when you want:

- persistent indexed documentation across sessions
- semantic search with `search_docs`
- exact DB-backed search with `grep_docs`
- a shared documentation index for a team or long-running agent setup

Skip it when you only need the normal `docpull` CLI or the default
`docpull mcp` server.

## Requirements

- Bun
- PostgreSQL with the `vector` extension available
- a database connection string in `DATABASE_URL`
- `OPENAI_API_KEY` for embedding generation
- the `docpull` CLI installed for fetching documentation

## Setup

```bash
cd mcp
bun install

export DATABASE_URL="postgresql://user:pass@host:5432/docs"
export OPENAI_API_KEY="sk-..."

bun run db:setup
bun run dev
```

`bun run db:setup` applies `schema.sql`, creates the migration tracking table,
and applies any pending migrations. `ensure_docs(..., index: true)` requires
both `DATABASE_URL` and `OPENAI_API_KEY`; `ensure_docs(..., index: false)` can
fetch docs without embedding them.

## Database Commands

```bash
bun run db:setup     # initialize schema and apply pending migrations
bun run db:migrate   # apply pending migrations only
bun run db:status    # show applied and pending migrations
bun run db:rollback  # roll back the latest applied migration
```

Applied migrations are tracked in `docpull_mcp_migrations`.

## MCP Usage

After the server is running, use the MCP tools:

```text
ensure_docs(source: "react", index: true)
search_docs(query: "how do effects work", library: "react")
grep_docs(pattern: "useEffect", library: "react")
list_indexed()
```

Indexing is explicit opt-in. `ensure_docs(source: "react")` fetches docs
without embedding them; pass `index: true` to write embeddings into Postgres.

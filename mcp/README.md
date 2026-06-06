# docpull-mcp

Optional TypeScript MCP server for fetching and searching documentation
on-demand with PostgreSQL, pgvector, and OpenAI embeddings.

Most users should use the Python stdio server shipped by the `docpull` package:

```bash
pip install 'docpull[mcp]'
docpull mcp
```

This `mcp/` directory is for advanced users who specifically need DB-backed
semantic search. It uses [docpull](https://github.com/raintree-technology/docpull)
as the fetcher and pgvector for search.

## Features

- **Fetch docs on-demand** - Pull documentation for built-in or configured libraries
- **Semantic search** - Find concepts even when you don't know exact terms
- **Exact matching** - Grep-like search for known function/method names
- **Built-in sources** - React, Next.js, Hono, Supabase, and more
- **Custom sources** - Add your own documentation URLs
- **7-day cache** - Automatic refresh of stale docs

## Install

```bash
git clone https://github.com/raintree-technology/docpull-mcp
cd docpull-mcp
bun install

# Requires the docpull CLI for fetching
pip install docpull
```

### Semantic search setup

```bash
# PostgreSQL with pgvector, then set environment variables.
export DATABASE_URL="postgresql://user:pass@localhost:5432/docs"
export OPENAI_API_KEY="sk-..."

# Create schema and apply migrations
bun run db:setup
```

Existing databases can apply only pending migrations:

```bash
bun run db:migrate
```

Useful database commands:

```bash
bun run db:status    # show applied and pending migrations
bun run db:rollback  # roll back the latest applied migration
```

The migration runner records applied migrations in
`docpull_mcp_migrations`. If you need to debug manually, the raw SQL files
remain in `schema.sql` and `migrations/*.sql`.

## Usage

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "docpull": {
      "command": "bun",
      "args": ["run", "/path/to/docpull-mcp/src/server.ts"],
      "env": {
        "DATABASE_URL": "postgresql://...",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "docpull": {
      "command": "bun",
      "args": ["run", "/path/to/docpull-mcp/src/server.ts"],
      "env": {
        "DATABASE_URL": "postgresql://...",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

## Tools

### ensure_docs

Fetch documentation for a configured source. Indexing is explicit opt-in and
requires both `DATABASE_URL` and `OPENAI_API_KEY`.

```
ensure_docs(source: "react")              # Fetch only
ensure_docs(source: "react", force: true) # Force refresh
ensure_docs(source: "react", index: true) # Fetch and index
```

Direct URLs are intentionally disabled for `ensure_docs`. Add custom sites to
`~/.config/docpull-mcp/sources.yaml` and call `ensure_docs` with the alias name.

### search_docs

Semantic search for concepts (requires DATABASE_URL + OPENAI_API_KEY).

```
search_docs(query: "how to stream responses", library: "anthropic")
search_docs(query: "row level security", library: "supabase")
```

### grep_docs

Fast exact pattern matching against indexed chunks (requires `DATABASE_URL`).

```
grep_docs(pattern: "onConflictDoUpdate", library: "drizzle")
grep_docs(pattern: "usePrefetchQuery")
```

### list_sources

List available documentation sources.

```
list_sources()                     # All sources
list_sources(category: "frontend") # Filter by category
```

### list_indexed

List libraries that have been indexed in Postgres for search.

```
list_indexed()
```

## Built-in Sources

| Name | Category | Description |
|------|----------|-------------|
| react | frontend | React documentation |
| nextjs | frontend | Next.js documentation |
| tailwindcss | frontend | Tailwind CSS |
| vite | frontend | Vite build tool |
| hono | backend | Hono web framework |
| fastapi | backend | FastAPI framework |
| express | backend | Express.js framework |
| anthropic | ai | Anthropic Claude API |
| openai | ai | OpenAI API |
| langchain | ai | LangChain framework |
| supabase | database | Supabase documentation |
| drizzle | database | Drizzle ORM |
| prisma | database | Prisma ORM |

## Custom Sources

Add to `~/.config/docpull-mcp/sources.yaml`:

```yaml
sources:
  my-docs:
    url: "https://docs.example.com"
    description: "My documentation"
    category: "internal"
    maxPages: 200
```

## Manual Ingestion

To ingest docs without the MCP server:

```bash
# Prepare the database first
bun run db:setup

# Ingest all fetched docs
bun run ingest

# Ingest specific library
bun run ingest react
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | For search | PostgreSQL connection string with pgvector |
| `OPENAI_API_KEY` | For search | OpenAI API key for embeddings |
| `DOCS_DIR` | No | Custom docs directory (default: `~/.local/share/docpull-mcp/docs`) |
| `OPENAI_TIMEOUT_MS` | No | Embedding request timeout in milliseconds (default: `30000`) |
| `OPENAI_MAX_RETRIES` | No | OpenAI SDK retry count for embedding calls (default: `2`) |
| `OPENAI_CIRCUIT_FAILURE_THRESHOLD` | No | Consecutive embedding failures before opening the circuit (default: `5`) |
| `OPENAI_CIRCUIT_RESET_MS` | No | Circuit reset window in milliseconds (default: `60000`) |
| `DB_POOL_MAX` | No | Maximum PostgreSQL pool size (default: `10`) |
| `DB_POOL_MIN` | No | Minimum PostgreSQL pool size (default: `2`) |
| `DB_IDLE_TIMEOUT_MS` | No | PostgreSQL idle connection timeout (default: `30000`) |
| `DB_CONNECTION_TIMEOUT_MS` | No | PostgreSQL connection timeout (default: `5000`) |
| `DB_STATEMENT_TIMEOUT_MS` | No | PostgreSQL statement/query timeout (default: `30000`) |

## Cost

- **Fetching**: Free (uses docpull)
- **Indexing**: ~$0.10 per 50,000 words (OpenAI embeddings)
- **Searching**: ~$0.0001 per query

## License

MIT

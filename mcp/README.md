# docpull-mcp

MCP server for fetching and searching documentation on-demand. Uses [docpull](https://github.com/raintree-technology/docpull) to pull docs and pgvector for semantic search.

## Features

- **Fetch docs on-demand** - Pull documentation for any library
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

# Requires docpull CLI
pip install docpull
```

### For semantic search (optional but recommended)

```bash
# PostgreSQL with pgvector
psql $DATABASE_URL -f schema.sql

# Set environment variables
export DATABASE_URL="postgresql://user:pass@localhost:5432/docs"
export OPENAI_API_KEY="sk-..."
```

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

Fetch and index documentation for a library.

```
ensure_docs(source: "react")              # Fetch and index
ensure_docs(source: "https://...")        # Direct URL
ensure_docs(source: "react", force: true) # Force refresh
ensure_docs(source: "react", index: false) # Fetch only, no indexing
```

### search_docs

Semantic search for concepts (requires DATABASE_URL + OPENAI_API_KEY).

```
search_docs(query: "how to stream responses", library: "anthropic")
search_docs(query: "row level security", library: "supabase")
```

### grep_docs

Fast exact pattern matching (requires DATABASE_URL).

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

List libraries that have been indexed for search.

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

## Cost

- **Fetching**: Free (uses docpull)
- **Indexing**: ~$0.10 per 50,000 words (OpenAI embeddings)
- **Searching**: ~$0.0001 per query

## License

MIT

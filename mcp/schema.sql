-- PostgreSQL schema for docpull-mcp
-- Requires PostgreSQL with pgvector extension

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Documentation embeddings table
CREATE TABLE IF NOT EXISTS doc_embeddings (
	id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
	library TEXT NOT NULL,
	file_path TEXT NOT NULL,
	chunk_index INTEGER NOT NULL,
	content TEXT NOT NULL,
	embedding vector(1536),  -- OpenAI text-embedding-3-small dimension
	metadata JSONB,
	created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes

-- Vector similarity search (HNSW)
CREATE INDEX IF NOT EXISTS idx_embedding
	ON doc_embeddings
	USING hnsw (embedding vector_cosine_ops);

-- Fast filtering by library
CREATE INDEX IF NOT EXISTS idx_library
	ON doc_embeddings(library);

-- Fast filtering by file path
CREATE INDEX IF NOT EXISTS idx_file_path
	ON doc_embeddings(file_path);

-- Prevent duplicate chunks
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_chunk
	ON doc_embeddings(library, file_path, chunk_index);

-- Optional: Fast text pattern matching (uncomment to enable)
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE INDEX IF NOT EXISTS idx_content_trgm
-- 	ON doc_embeddings USING gin(content gin_trgm_ops);

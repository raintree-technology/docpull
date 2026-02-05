/**
 * PostgreSQL client for documentation embeddings with pgvector
 */

import { Pool } from "pg";

// ============================================================================
// SETUP
// ============================================================================

const DATABASE_URL = process.env.DATABASE_URL;
const DB_POOL_MAX = Number.parseInt(process.env.DB_POOL_MAX || "10", 10);
const DB_POOL_MIN = Number.parseInt(process.env.DB_POOL_MIN || "2", 10);
const DB_IDLE_TIMEOUT_MS = Number.parseInt(
	process.env.DB_IDLE_TIMEOUT_MS || "30000",
	10,
);
const DB_CONNECTION_TIMEOUT_MS = Number.parseInt(
	process.env.DB_CONNECTION_TIMEOUT_MS || "5000",
	10,
);
const DB_STATEMENT_TIMEOUT_MS = Number.parseInt(
	process.env.DB_STATEMENT_TIMEOUT_MS || "30000",
	10,
);

let pool: Pool | null = null;

export function getPool(): Pool {
	if (!DATABASE_URL) {
		throw new Error("DATABASE_URL environment variable is required");
	}

	if (!pool) {
		pool = new Pool({
			connectionString: DATABASE_URL,
			max: DB_POOL_MAX,
			min: DB_POOL_MIN,
			idleTimeoutMillis: DB_IDLE_TIMEOUT_MS,
			connectionTimeoutMillis: DB_CONNECTION_TIMEOUT_MS,
			statement_timeout: DB_STATEMENT_TIMEOUT_MS,
			query_timeout: DB_STATEMENT_TIMEOUT_MS,
		});

		pool.on("error", (err) => {
			console.error("Unexpected database pool error:", err);
		});
	}

	return pool;
}

export function isDbConfigured(): boolean {
	return !!DATABASE_URL;
}

// Graceful shutdown
process.on("SIGTERM", async () => {
	if (pool) {
		console.error("SIGTERM received, closing database pool...");
		await pool.end();
	}
});

process.on("SIGINT", async () => {
	if (pool) {
		console.error("SIGINT received, closing database pool...");
		await pool.end();
	}
});

// ============================================================================
// TYPES
// ============================================================================

export interface SearchResult {
	library: string;
	file_path: string;
	content: string;
	similarity: number;
	metadata: Record<string, unknown> | null;
}

export interface GrepResult {
	library: string;
	file_path: string;
	content: string;
}

export interface LibraryInfo {
	library: string;
	chunks: number;
}

// ============================================================================
// DATABASE OPERATIONS
// ============================================================================

/**
 * Insert multiple embeddings in a single transaction
 */
export async function insertEmbeddingsBatch(
	docs: Array<{
		library: string;
		file_path: string;
		chunk_index: number;
		content: string;
		embedding: number[];
		metadata?: Record<string, unknown> | null;
	}>,
): Promise<void> {
	if (docs.length === 0) return;

	const p = getPool();
	const client = await p.connect();
	try {
		await client.query("BEGIN");

		const values: string[] = [];
		const params: unknown[] = [];
		let paramIndex = 1;

		for (const doc of docs) {
			values.push(
				`($${paramIndex}, $${paramIndex + 1}, $${paramIndex + 2}, $${paramIndex + 3}, $${paramIndex + 4}, $${paramIndex + 5})`,
			);
			params.push(
				doc.library,
				doc.file_path,
				doc.chunk_index,
				doc.content,
				JSON.stringify(doc.embedding),
				doc.metadata ? JSON.stringify(doc.metadata) : null,
			);
			paramIndex += 6;
		}

		await client.query(
			`INSERT INTO doc_embeddings (library, file_path, chunk_index, content, embedding, metadata)
			 VALUES ${values.join(", ")}`,
			params,
		);

		await client.query("COMMIT");
	} catch (error) {
		await client.query("ROLLBACK");
		throw error;
	} finally {
		client.release();
	}
}

/**
 * Delete all embeddings for a specific library
 */
export async function deleteLibraryDocs(library: string): Promise<number> {
	const p = getPool();
	const result = await p.query(
		"DELETE FROM doc_embeddings WHERE library = $1",
		[library],
	);
	return result.rowCount ?? 0;
}

/**
 * Search for documents using semantic similarity (cosine distance)
 */
export async function searchDocs(
	queryEmbedding: number[],
	options: {
		library?: string;
		limit?: number;
		minSimilarity?: number;
	} = {},
): Promise<SearchResult[]> {
	const { library, limit = 5, minSimilarity = 0 } = options;

	if (!Array.isArray(queryEmbedding) || queryEmbedding.length === 0) {
		throw new Error("Query embedding must be a non-empty array");
	}

	const embeddingString = `[${queryEmbedding.join(",")}]`;

	const query = library
		? `SELECT library, file_path, content, metadata,
				  1 - (embedding <=> $1::vector) as similarity
		   FROM doc_embeddings
		   WHERE library = $2
		   ORDER BY embedding <=> $1::vector
		   LIMIT $3`
		: `SELECT library, file_path, content, metadata,
				  1 - (embedding <=> $1::vector) as similarity
		   FROM doc_embeddings
		   ORDER BY embedding <=> $1::vector
		   LIMIT $2`;

	const params = library
		? [embeddingString, library, limit]
		: [embeddingString, limit];

	const p = getPool();
	const result = await p.query(query, params);

	return result.rows
		.map((row) => ({
			library: row.library,
			file_path: row.file_path,
			content: row.content,
			similarity: Number.parseFloat(row.similarity),
			metadata: row.metadata,
		}))
		.filter((r) => r.similarity >= minSimilarity);
}

/**
 * Search for exact text patterns (grep-like, case-insensitive)
 */
export async function grepDocs(
	pattern: string,
	options: {
		library?: string;
		limit?: number;
	} = {},
): Promise<GrepResult[]> {
	const { library, limit = 5 } = options;

	if (!pattern || typeof pattern !== "string") {
		throw new Error("Pattern must be a non-empty string");
	}

	const query = library
		? `SELECT library, file_path, content
		   FROM doc_embeddings
		   WHERE content ILIKE $1 AND library = $2
		   LIMIT $3`
		: `SELECT library, file_path, content
		   FROM doc_embeddings
		   WHERE content ILIKE $1
		   LIMIT $2`;

	const params = library
		? [`%${pattern}%`, library, limit]
		: [`%${pattern}%`, limit];

	const p = getPool();
	const result = await p.query(query, params);

	return result.rows.map((row) => ({
		library: row.library,
		file_path: row.file_path,
		content: row.content,
	}));
}

/**
 * List all available libraries with chunk counts
 */
export async function listLibraries(): Promise<LibraryInfo[]> {
	const p = getPool();
	const result = await p.query(`
		SELECT library, COUNT(*) as chunks
		FROM doc_embeddings
		GROUP BY library
		ORDER BY library
	`);

	return result.rows.map((row) => ({
		library: row.library,
		chunks: Number.parseInt(row.chunks, 10),
	}));
}

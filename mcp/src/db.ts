/**
 * PostgreSQL client for documentation embeddings with pgvector.
 */

import { Pool } from "pg";
import { EMBEDDING_DIMENSIONS } from "./embeddings.js";
import { errorMessage, logStructured } from "./logger.js";

// ============================================================================
// SETUP
// ============================================================================

const DATABASE_URL = process.env.DATABASE_URL;
const DEFAULT_DB_POOL_MAX = 10;
const DEFAULT_DB_POOL_MIN = 2;
const DEFAULT_DB_IDLE_TIMEOUT_MS = 30_000;
const DEFAULT_DB_CONNECTION_TIMEOUT_MS = 5_000;
const DEFAULT_DB_STATEMENT_TIMEOUT_MS = 30_000;
const SEARCH_LIMIT_DEFAULT = 5;
const SEARCH_LIMIT_MAX = 50;
const GREP_LIMIT_DEFAULT = 5;
const GREP_LIMIT_MAX = 50;

function readIntegerEnv(
	name: string,
	defaultValue: number,
	{ min, max }: { min: number; max: number },
): number {
	const raw = process.env[name];
	if (raw === undefined || raw === "") {
		return defaultValue;
	}
	const parsed = Number.parseInt(raw, 10);
	if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
		throw new Error(`${name} must be an integer between ${min} and ${max}`);
	}
	return parsed;
}

const DB_POOL_MAX = readIntegerEnv("DB_POOL_MAX", DEFAULT_DB_POOL_MAX, {
	min: 1,
	max: 100,
});
const DB_POOL_MIN = readIntegerEnv("DB_POOL_MIN", DEFAULT_DB_POOL_MIN, {
	min: 0,
	max: DB_POOL_MAX,
});
const DB_IDLE_TIMEOUT_MS = readIntegerEnv(
	"DB_IDLE_TIMEOUT_MS",
	DEFAULT_DB_IDLE_TIMEOUT_MS,
	{ min: 1_000, max: 3_600_000 },
);
const DB_CONNECTION_TIMEOUT_MS = readIntegerEnv(
	"DB_CONNECTION_TIMEOUT_MS",
	DEFAULT_DB_CONNECTION_TIMEOUT_MS,
	{ min: 1_000, max: 300_000 },
);
const DB_STATEMENT_TIMEOUT_MS = readIntegerEnv(
	"DB_STATEMENT_TIMEOUT_MS",
	DEFAULT_DB_STATEMENT_TIMEOUT_MS,
	{ min: 1_000, max: 300_000 },
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

		pool.on("error", (error) => {
			logStructured("error", "Unexpected database pool error", {
				error: errorMessage(error),
			});
		});
	}

	return pool;
}

export function isDbConfigured(): boolean {
	return !!DATABASE_URL;
}

async function closePool(signal: string): Promise<void> {
	if (pool) {
		logStructured("info", "Closing database pool", { signal });
		await pool.end();
		pool = null;
	}
}

process.on("SIGTERM", () => {
	void closePool("SIGTERM");
});

process.on("SIGINT", () => {
	void closePool("SIGINT");
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

export interface EmbeddingDocument {
	library: string;
	file_path: string;
	chunk_index: number;
	content: string;
	embedding: number[];
	metadata?: Record<string, unknown> | null;
}

export interface QueryResultShape {
	rowCount: number | null;
	rows: Array<Record<string, unknown>>;
}

export interface DbClient {
	query(sql: string, params?: readonly unknown[]): Promise<QueryResultShape>;
	release?(): void;
}

// ============================================================================
// VALIDATION
// ============================================================================

function requirePositiveLimit(value: number, max: number, name: string): number {
	if (!Number.isInteger(value) || value < 1 || value > max) {
		throw new Error(`${name} must be an integer between 1 and ${max}`);
	}
	return value;
}

function validateEmbeddingVector(embedding: number[], name: string): void {
	if (embedding.length !== EMBEDDING_DIMENSIONS) {
		throw new Error(
			`${name} must contain ${EMBEDDING_DIMENSIONS} dimensions, got ${embedding.length}`,
		);
	}
	for (const value of embedding) {
		if (!Number.isFinite(value)) {
			throw new Error(`${name} contains a non-finite value`);
		}
	}
}

function vectorLiteral(embedding: number[]): string {
	validateEmbeddingVector(embedding, "embedding");
	return `[${embedding.join(",")}]`;
}

function validateEmbeddingDocuments(
	library: string | null,
	docs: readonly EmbeddingDocument[],
): void {
	for (const doc of docs) {
		if (library !== null && doc.library !== library) {
			throw new Error(
				`Embedding document library mismatch: expected '${library}', got '${doc.library}'`,
			);
		}
		if (!doc.library || !doc.file_path || !doc.content) {
			throw new Error("Embedding documents require library, file_path, and content");
		}
		if (!Number.isInteger(doc.chunk_index) || doc.chunk_index < 0) {
			throw new Error("Embedding document chunk_index must be a non-negative integer");
		}
		validateEmbeddingVector(doc.embedding, "document embedding");
	}
}

// ============================================================================
// DATABASE OPERATIONS
// ============================================================================

async function insertEmbeddingRows(
	client: DbClient,
	docs: readonly EmbeddingDocument[],
): Promise<void> {
	if (docs.length === 0) {
		return;
	}

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
			vectorLiteral(doc.embedding),
			doc.metadata ? JSON.stringify(doc.metadata) : null,
		);
		paramIndex += 6;
	}

	await client.query(
		`INSERT INTO doc_embeddings (library, file_path, chunk_index, content, embedding, metadata)
		 VALUES ${values.join(", ")}`,
		params,
	);
}

/**
 * Insert multiple embeddings in a single transaction.
 */
export async function insertEmbeddingsBatch(
	docs: readonly EmbeddingDocument[],
): Promise<void> {
	if (docs.length === 0) {
		return;
	}
	validateEmbeddingDocuments(null, docs);

	const client = await getPool().connect();
	try {
		await client.query("BEGIN");
		await insertEmbeddingRows(client, docs);
		await client.query("COMMIT");
	} catch (error) {
		await client.query("ROLLBACK");
		throw error;
	} finally {
		client.release();
	}
}

/**
 * Atomically replace one library's derived embedding cache.
 *
 * The hard delete is limited to derived rows for a single library and happens
 * inside the same transaction as the replacement insert, so a failed refresh
 * cannot leave the library with an empty or partial index.
 */
export async function replaceLibraryEmbeddingsWithClient(
	client: DbClient,
	library: string,
	docs: readonly EmbeddingDocument[],
): Promise<void> {
	validateEmbeddingDocuments(library, docs);

	await client.query("BEGIN");
	try {
		await client.query("DELETE FROM doc_embeddings WHERE library = $1", [
			library,
		]);
		await insertEmbeddingRows(client, docs);
		await client.query("COMMIT");
	} catch (error) {
		await client.query("ROLLBACK");
		throw error;
	}
}

export async function replaceLibraryEmbeddings(
	library: string,
	docs: readonly EmbeddingDocument[],
): Promise<void> {
	const client = await getPool().connect();
	try {
		await replaceLibraryEmbeddingsWithClient(client, library, docs);
	} finally {
		client.release?.();
	}
}

/**
 * Search for documents using semantic similarity (cosine distance).
 */
export async function searchDocs(
	queryEmbedding: number[],
	options: {
		library?: string;
		limit?: number;
		minSimilarity?: number;
	} = {},
): Promise<SearchResult[]> {
	const {
		library,
		limit = SEARCH_LIMIT_DEFAULT,
		minSimilarity = 0,
	} = options;

	validateEmbeddingVector(queryEmbedding, "Query embedding");
	const safeLimit = requirePositiveLimit(limit, SEARCH_LIMIT_MAX, "limit");
	if (!Number.isFinite(minSimilarity) || minSimilarity < 0 || minSimilarity > 1) {
		throw new Error("minSimilarity must be a number between 0 and 1");
	}

	const embeddingString = vectorLiteral(queryEmbedding);

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
		? [embeddingString, library, safeLimit]
		: [embeddingString, safeLimit];

	const result = await getPool().query(query, params);

	return result.rows
		.map((row) => ({
			library: String(row.library),
			file_path: String(row.file_path),
			content: String(row.content),
			similarity: Number.parseFloat(String(row.similarity)),
			metadata: row.metadata as Record<string, unknown> | null,
		}))
		.filter((resultRow) => resultRow.similarity >= minSimilarity);
}

/**
 * Escape ILIKE metacharacters so the pattern matches literally.
 */
export function escapeIlikePattern(raw: string): string {
	return raw.replace(/\\/g, "\\\\").replace(/%/g, "\\%").replace(/_/g, "\\_");
}

/**
 * Search for exact text patterns (grep-like, case-insensitive).
 */
export async function grepDocs(
	pattern: string,
	options: {
		library?: string;
		limit?: number;
	} = {},
): Promise<GrepResult[]> {
	const { library, limit = GREP_LIMIT_DEFAULT } = options;

	if (!pattern || typeof pattern !== "string") {
		throw new Error("Pattern must be a non-empty string");
	}
	const safeLimit = requirePositiveLimit(limit, GREP_LIMIT_MAX, "limit");
	const escaped = escapeIlikePattern(pattern);

	const query = library
		? `SELECT library, file_path, content
		   FROM doc_embeddings
		   WHERE content ILIKE $1 ESCAPE '\\' AND library = $2
		   LIMIT $3`
		: `SELECT library, file_path, content
		   FROM doc_embeddings
		   WHERE content ILIKE $1 ESCAPE '\\'
		   LIMIT $2`;

	const params = library
		? [`%${escaped}%`, library, safeLimit]
		: [`%${escaped}%`, safeLimit];

	const result = await getPool().query(query, params);

	return result.rows.map((row) => ({
		library: String(row.library),
		file_path: String(row.file_path),
		content: String(row.content),
	}));
}

/**
 * List all available libraries with chunk counts.
 */
export async function listLibraries(): Promise<LibraryInfo[]> {
	const result = await getPool().query(`
		SELECT library, COUNT(*) as chunks
		FROM doc_embeddings
		GROUP BY library
		ORDER BY library
	`);

	return result.rows.map((row) => ({
		library: String(row.library),
		chunks: Number.parseInt(String(row.chunks), 10),
	}));
}

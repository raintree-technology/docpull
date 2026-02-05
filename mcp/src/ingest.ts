#!/usr/bin/env bun
/**
 * Documentation Ingestion Script
 * Chunks and embeds markdown files, then stores in pgvector
 */

import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { OpenAI } from "openai";
import { deleteLibraryDocs, insertEmbeddingsBatch } from "./db.js";

// ============================================================================
// CONFIG
// ============================================================================

const DEFAULT_DOCS_DIR = join(
	homedir(),
	".local",
	"share",
	"docpull-mcp",
	"docs",
);
const DOCS_DIR = process.env.DOCS_DIR || DEFAULT_DOCS_DIR;

const CHUNK_SIZE = 1000; // tokens (roughly 750 words)
const CHUNK_OVERLAP = 200; // tokens
const MAX_BATCH_TOKENS = 7000; // Leave headroom under OpenAI's 8192 limit
const MAX_BATCH_SIZE = 100;

function getOpenAI(): OpenAI {
	const key = process.env.OPENAI_API_KEY;
	if (!key) {
		throw new Error("OPENAI_API_KEY environment variable required");
	}
	return new OpenAI({ apiKey: key });
}

// ============================================================================
// UTILITIES
// ============================================================================

function estimateTokens(text: string): number {
	return Math.ceil(text.length / 4);
}

function chunkText(text: string, maxTokens: number, overlap: number): string[] {
	const chunks: string[] = [];
	const lines = text.split("\n");
	let currentChunk: string[] = [];
	let currentTokens = 0;

	for (const line of lines) {
		const lineTokens = estimateTokens(line);

		if (currentTokens + lineTokens > maxTokens && currentChunk.length > 0) {
			chunks.push(currentChunk.join("\n"));

			const overlapLines: string[] = [];
			let overlapTokens = 0;
			for (let i = currentChunk.length - 1; i >= 0; i--) {
				const tokens = estimateTokens(currentChunk[i]);
				if (overlapTokens + tokens > overlap) break;
				overlapLines.unshift(currentChunk[i]);
				overlapTokens += tokens;
			}

			currentChunk = overlapLines;
			currentTokens = overlapTokens;
		}

		currentChunk.push(line);
		currentTokens += lineTokens;
	}

	if (currentChunk.length > 0) {
		chunks.push(currentChunk.join("\n"));
	}

	return chunks;
}

function extractHeading(chunk: string): string | undefined {
	const match = chunk.match(/^#+\s+(.+)$/m);
	return match?.[1];
}

interface ChunkData {
	library: string;
	filePath: string;
	chunkIndex: number;
	content: string;
	metadata: {
		heading?: string;
		tokens: number;
		source: string;
	};
}

function batchByTokens(chunks: ChunkData[]): ChunkData[][] {
	const batches: ChunkData[][] = [];
	let currentBatch: ChunkData[] = [];
	let currentTokens = 0;

	for (const chunk of chunks) {
		const tokens = chunk.metadata.tokens;

		if (
			(currentTokens + tokens > MAX_BATCH_TOKENS ||
				currentBatch.length >= MAX_BATCH_SIZE) &&
			currentBatch.length > 0
		) {
			batches.push(currentBatch);
			currentBatch = [];
			currentTokens = 0;
		}

		currentBatch.push(chunk);
		currentTokens += tokens;
	}

	if (currentBatch.length > 0) {
		batches.push(currentBatch);
	}

	return batches;
}

function getAllMarkdownFiles(
	dir: string,
	library: string,
): Array<{ path: string; library: string }> {
	const files: Array<{ path: string; library: string }> = [];

	function traverse(currentDir: string, lib: string) {
		const entries = readdirSync(currentDir);

		for (const entry of entries) {
			const fullPath = join(currentDir, entry);
			const stat = statSync(fullPath);

			if (stat.isDirectory()) {
				traverse(fullPath, lib);
			} else if (entry.endsWith(".md")) {
				files.push({ path: fullPath, library: lib });
			}
		}
	}

	traverse(dir, library);
	return files;
}

// ============================================================================
// INGESTION
// ============================================================================

async function ingestLibrary(libraryPath: string, libraryName: string) {
	console.log(`\nIngesting ${libraryName}...`);

	const files = getAllMarkdownFiles(libraryPath, libraryName);
	console.log(`Found ${files.length} markdown files`);

	if (files.length === 0) {
		console.log(`Skipping ${libraryName} - no markdown files`);
		return;
	}

	await deleteLibraryDocs(libraryName);
	console.log(`Cleared existing ${libraryName} embeddings`);

	const allChunks: ChunkData[] = [];

	for (const file of files) {
		const content = readFileSync(file.path, "utf-8");
		const chunks = chunkText(content, CHUNK_SIZE, CHUNK_OVERLAP);
		const relativePath = file.path.replace(libraryPath + "/", "");

		for (let i = 0; i < chunks.length; i++) {
			const chunk = chunks[i];
			const heading = extractHeading(chunk);

			allChunks.push({
				library: libraryName,
				filePath: relativePath,
				chunkIndex: i,
				content: chunk,
				metadata: {
					heading,
					tokens: estimateTokens(chunk),
					source: file.path,
				},
			});
		}
	}

	console.log(`Created ${allChunks.length} chunks`);

	const batches = batchByTokens(allChunks);
	console.log(`Generating embeddings in ${batches.length} batches...`);

	const openai = getOpenAI();
	let processed = 0;
	for (const batch of batches) {
		const texts = batch.map((e) => e.content);

		const response = await openai.embeddings.create({
			model: "text-embedding-3-small",
			input: texts,
		});

		await insertEmbeddingsBatch(
			batch.map((item, idx) => ({
				library: item.library,
				file_path: item.filePath,
				chunk_index: item.chunkIndex,
				content: item.content,
				embedding: response.data[idx].embedding,
				metadata: item.metadata,
			})),
		);

		processed += batch.length;
		console.log(`  Processed ${processed}/${allChunks.length}`);
	}

	console.log(`${libraryName} complete: ${allChunks.length} chunks embedded`);
}

export async function ingestSingleLibrary(libraryName: string): Promise<void> {
	const libraryPath = join(DOCS_DIR, libraryName);
	if (!existsSync(libraryPath)) {
		throw new Error(`Library not found: ${libraryPath}`);
	}
	await ingestLibrary(libraryPath, libraryName);
}

async function main() {
	console.log("docpull-mcp Documentation Ingestion");
	console.log("=".repeat(50));
	console.log(`Docs directory: ${DOCS_DIR}`);
	console.log("");

	if (!existsSync(DOCS_DIR)) {
		console.error(`Error: Docs directory not found: ${DOCS_DIR}`);
		console.error("\nFetch some docs first with ensure_docs tool, or run:");
		console.error(
			"  docpull https://example.com/docs -o ~/.local/share/docpull-mcp/docs/example",
		);
		process.exit(1);
	}

	// Get specific library from args, or ingest all
	const targetLibrary = process.argv[2];

	if (targetLibrary) {
		const libraryPath = join(DOCS_DIR, targetLibrary);
		if (!existsSync(libraryPath)) {
			console.error(`Error: Library not found: ${libraryPath}`);
			process.exit(1);
		}
		await ingestLibrary(libraryPath, targetLibrary);
	} else {
		const libraries = readdirSync(DOCS_DIR).filter((name) => {
			const stat = statSync(join(DOCS_DIR, name));
			return stat.isDirectory();
		});

		if (libraries.length === 0) {
			console.error(`Error: No library directories found in ${DOCS_DIR}`);
			process.exit(1);
		}

		console.log(`Found ${libraries.length} libraries:`);
		libraries.forEach((lib) => console.log(`  - ${lib}`));

		for (const library of libraries) {
			try {
				await ingestLibrary(join(DOCS_DIR, library), library);
			} catch (error) {
				console.error(`Error ingesting ${library}:`, error);
			}
		}
	}

	console.log("\nIngestion complete!");
	process.exit(0);
}

// Only run main if executed directly
if (import.meta.main) {
	main().catch((error) => {
		console.error("Fatal error:", error);
		process.exit(1);
	});
}

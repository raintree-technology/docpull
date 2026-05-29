#!/usr/bin/env bun
/**
 * Documentation Ingestion Script
 * Chunks and embeds markdown files, then stores in pgvector
 */

import {
	existsSync,
	lstatSync,
	readdirSync,
	readFileSync,
	statSync,
} from "node:fs";
import { join, relative, resolve } from "node:path";
import { homedir } from "node:os";
import {
	replaceLibraryEmbeddings,
	type EmbeddingDocument,
} from "./db.js";
import {
	createEmbeddings,
	requireConfiguredOpenAIClient,
} from "./embeddings.js";
import { errorMessage, logStructured } from "./logger.js";
import { isSafeSourceName } from "./source_resolver.js";

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

const CHUNK_SIZE_TOKENS = 1_000;
const CHUNK_OVERLAP_TOKENS = 200;
const MAX_BATCH_TOKENS = 7_000;
const MAX_BATCH_SIZE = 100;
const EXIT_SUCCESS = 0;
const EXIT_FAILURE = 1;

function writeLine(message = ""): void {
	process.stdout.write(`${message}\n`);
}

function writeErrorLine(message: string): void {
	process.stderr.write(`${message}\n`);
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

function resolveLibraryPath(libraryName: string): string {
	if (!isSafeSourceName(libraryName)) {
		throw new Error(`Invalid library name: ${libraryName}`);
	}
	const docsRoot = resolve(DOCS_DIR);
	const libraryPath = resolve(docsRoot, libraryName);
	const rel = relative(docsRoot, libraryPath);
	if (rel === "" || rel.startsWith("..")) {
		throw new Error(`Library path escapes docs directory: ${libraryName}`);
	}
	return libraryPath;
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
			const stat = lstatSync(fullPath);

			if (stat.isSymbolicLink()) {
				logStructured("warn", "Skipping symlink during ingestion", {
					library: lib,
					path: fullPath,
				});
			} else if (stat.isDirectory()) {
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

async function ingestLibrary(libraryPath: string, libraryName: string): Promise<void> {
	writeLine(`\nIngesting ${libraryName}...`);

	const files = getAllMarkdownFiles(libraryPath, libraryName);
	writeLine(`Found ${files.length} markdown files`);

	if (files.length === 0) {
		writeLine(`Skipping ${libraryName} - no markdown files`);
		return;
	}

	const allChunks: ChunkData[] = [];

	for (const file of files) {
		const content = readFileSync(file.path, "utf-8");
		const chunks = chunkText(content, CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS);
		const relativePath = relative(libraryPath, file.path);

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

	writeLine(`Created ${allChunks.length} chunks`);

	const batches = batchByTokens(allChunks);
	writeLine(`Generating embeddings in ${batches.length} batches...`);

	const openai = requireConfiguredOpenAIClient();
	let processed = 0;
	const docs: EmbeddingDocument[] = [];
	for (const batch of batches) {
		const texts = batch.map((e) => e.content);
		const embeddings = await createEmbeddings(openai, texts);

		docs.push(
			...batch.map((item, idx) => ({
				library: item.library,
				file_path: item.filePath,
				chunk_index: item.chunkIndex,
				content: item.content,
				embedding: embeddings[idx],
				metadata: item.metadata,
			})),
		);

		processed += batch.length;
		writeLine(`  Processed ${processed}/${allChunks.length}`);
	}

	await replaceLibraryEmbeddings(libraryName, docs);
	writeLine(`${libraryName} complete: ${allChunks.length} chunks embedded`);
}

export async function ingestSingleLibrary(libraryName: string): Promise<void> {
	const libraryPath = resolveLibraryPath(libraryName);
	if (!existsSync(libraryPath)) {
		throw new Error(`Library not found: ${libraryPath}`);
	}
	await ingestLibrary(libraryPath, libraryName);
}

async function main(): Promise<number> {
	writeLine("docpull-mcp Documentation Ingestion");
	writeLine("=".repeat(50));
	writeLine(`Docs directory: ${DOCS_DIR}`);
	writeLine();

	if (!existsSync(DOCS_DIR)) {
		writeErrorLine(`Error: Docs directory not found: ${DOCS_DIR}`);
		writeErrorLine("\nFetch some docs first with ensure_docs tool, or run:");
		writeErrorLine(
			"  docpull https://example.com/docs -o ~/.local/share/docpull-mcp/docs/example",
		);
		return EXIT_FAILURE;
	}

	const targetLibrary = process.argv[2];

	if (targetLibrary) {
		const libraryPath = resolveLibraryPath(targetLibrary);
		if (!existsSync(libraryPath)) {
			writeErrorLine(`Error: Library not found: ${libraryPath}`);
			return EXIT_FAILURE;
		}
		await ingestLibrary(libraryPath, targetLibrary);
		return EXIT_SUCCESS;
	} else {
		const libraries = readdirSync(DOCS_DIR).filter((name) => {
			if (!isSafeSourceName(name)) {
				logStructured("warn", "Skipping unsafe library directory name", {
					library: name,
				});
				return false;
			}
			const stat = statSync(join(DOCS_DIR, name));
			return stat.isDirectory();
		});

		if (libraries.length === 0) {
			writeErrorLine(`Error: No library directories found in ${DOCS_DIR}`);
			return EXIT_FAILURE;
		}

		writeLine(`Found ${libraries.length} libraries:`);
		for (const library of libraries) {
			writeLine(`  - ${library}`);
		}

		const failures: string[] = [];
		for (const library of libraries) {
			try {
				await ingestLibrary(join(DOCS_DIR, library), library);
			} catch (error) {
				failures.push(library);
				writeErrorLine(`Error ingesting ${library}: ${errorMessage(error)}`);
			}
		}

		if (failures.length > 0) {
			writeErrorLine(`Ingestion failed for ${failures.length} libraries.`);
			return EXIT_FAILURE;
		}
	}

	writeLine("\nIngestion complete!");
	return EXIT_SUCCESS;
}

if (import.meta.main) {
	main()
		.then((exitCode) => {
			process.exit(exitCode);
		})
		.catch((error) => {
			writeErrorLine(`Fatal error: ${errorMessage(error)}`);
			process.exit(EXIT_FAILURE);
		});
}

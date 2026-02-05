#!/usr/bin/env bun
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { spawn } from "child_process";
import {
	existsSync,
	readFileSync,
	writeFileSync,
	mkdirSync,
	statSync,
} from "fs";
import { readdir, stat } from "fs/promises";
import { join } from "path";
import { homedir } from "os";
import { parse as parseYaml } from "yaml";
import { OpenAI } from "openai";
import { isDbConfigured, searchDocs, grepDocs, listLibraries } from "./db.js";
import { ingestSingleLibrary } from "./ingest.js";

// ============================================================================
// CONFIG
// ============================================================================

const DOCS_DIR =
	process.env.DOCS_DIR ||
	join(homedir(), ".local", "share", "docpull-mcp", "docs");
const CONFIG_DIR = join(homedir(), ".config", "docpull-mcp");
const META_DIR = join(CONFIG_DIR, "meta");
const CACHE_TTL_DAYS = 7;
const DOCPULL_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

const OPENAI_KEY = process.env.OPENAI_API_KEY;
const openai = OPENAI_KEY ? new OpenAI({ apiKey: OPENAI_KEY }) : null;

// ============================================================================
// SOURCE CONFIG
// ============================================================================

interface SourceConfig {
	url: string;
	description: string;
	category: string;
	maxPages?: number;
}

const BUILTIN_SOURCES: Record<string, SourceConfig> = {
	// Frontend
	react: {
		url: "https://react.dev",
		description: "React documentation",
		category: "frontend",
		maxPages: 500,
	},
	nextjs: {
		url: "https://nextjs.org/docs",
		description: "Next.js documentation",
		category: "frontend",
		maxPages: 800,
	},
	tailwindcss: {
		url: "https://tailwindcss.com/docs",
		description: "Tailwind CSS",
		category: "frontend",
		maxPages: 300,
	},
	vite: {
		url: "https://vite.dev/guide",
		description: "Vite build tool",
		category: "frontend",
		maxPages: 200,
	},
	// Backend
	hono: {
		url: "https://hono.dev/docs",
		description: "Hono web framework",
		category: "backend",
		maxPages: 200,
	},
	fastapi: {
		url: "https://fastapi.tiangolo.com",
		description: "FastAPI framework",
		category: "backend",
		maxPages: 400,
	},
	express: {
		url: "https://expressjs.com",
		description: "Express.js framework",
		category: "backend",
		maxPages: 200,
	},
	// AI
	anthropic: {
		url: "https://docs.anthropic.com",
		description: "Anthropic Claude API",
		category: "ai",
		maxPages: 200,
	},
	openai: {
		url: "https://platform.openai.com/docs",
		description: "OpenAI API",
		category: "ai",
		maxPages: 400,
	},
	langchain: {
		url: "https://python.langchain.com/docs",
		description: "LangChain framework",
		category: "ai",
		maxPages: 1000,
	},
	// Database
	supabase: {
		url: "https://supabase.com/docs",
		description: "Supabase documentation",
		category: "database",
		maxPages: 600,
	},
	drizzle: {
		url: "https://orm.drizzle.team/docs",
		description: "Drizzle ORM",
		category: "database",
		maxPages: 300,
	},
	prisma: {
		url: "https://www.prisma.io/docs",
		description: "Prisma ORM",
		category: "database",
		maxPages: 500,
	},
};

// Cache for user sources - reloads only when file changes
let userSourcesCache: Record<string, SourceConfig> | null = null;
let userSourcesMtime: number = 0;

function loadUserSources(): Record<string, SourceConfig> {
	const p = join(CONFIG_DIR, "sources.yaml");
	if (!existsSync(p)) {
		userSourcesCache = {};
		return {};
	}
	try {
		const mtime = statSync(p).mtime.getTime();
		if (userSourcesCache && mtime === userSourcesMtime) {
			return userSourcesCache;
		}
		const parsed = parseYaml(readFileSync(p, "utf-8")) as {
			sources?: Record<string, SourceConfig>;
		};
		userSourcesCache = parsed.sources || {};
		userSourcesMtime = mtime;
		return userSourcesCache;
	} catch (e) {
		console.error("Failed to parse sources.yaml:", e);
		return userSourcesCache || {};
	}
}

function getAllSources() {
	return { ...BUILTIN_SOURCES, ...loadUserSources() };
}

// ============================================================================
// CACHE MANAGEMENT
// ============================================================================

interface FetchMeta {
	fetchedAt: number;
	fileCount: number;
	indexed?: boolean;
}

function getMetaPath(source: string) {
	return join(META_DIR, `${source}.json`);
}

function readMeta(source: string): FetchMeta | null {
	const p = getMetaPath(source);
	if (!existsSync(p)) return null;
	try {
		return JSON.parse(readFileSync(p, "utf-8"));
	} catch {
		return null;
	}
}

function writeMeta(source: string, meta: FetchMeta) {
	mkdirSync(META_DIR, { recursive: true });
	writeFileSync(getMetaPath(source), JSON.stringify(meta));
}

async function countMarkdownFiles(dir: string): Promise<number> {
	if (!existsSync(dir)) return 0;
	let count = 0;
	const entries = await readdir(dir, { withFileTypes: true });
	const promises: Promise<number>[] = [];
	for (const entry of entries) {
		const fullPath = join(dir, entry.name);
		if (entry.isDirectory()) {
			promises.push(countMarkdownFiles(fullPath));
		} else if (entry.name.endsWith(".md")) {
			count++;
		}
	}
	const subCounts = await Promise.all(promises);
	return count + subCounts.reduce((a, b) => a + b, 0);
}

type CacheInfo =
	| { exists: false }
	| {
			exists: true;
			fetchedAt: Date;
			fileCount: number;
			isStale: boolean;
			indexed: boolean;
	  };

async function getCacheInfo(source: string): Promise<CacheInfo> {
	const dir = join(DOCS_DIR, source);
	if (!existsSync(dir)) return { exists: false };

	const meta = readMeta(source);
	if (meta) {
		const isStale = Date.now() - meta.fetchedAt > CACHE_TTL_DAYS * 86400000;
		return {
			exists: true,
			fetchedAt: new Date(meta.fetchedAt),
			fileCount: meta.fileCount,
			isStale,
			indexed: meta.indexed ?? false,
		};
	}

	const dirStat = await stat(dir);
	const fileCount = await countMarkdownFiles(dir);
	const isStale =
		Date.now() - dirStat.mtime.getTime() > CACHE_TTL_DAYS * 86400000;
	return {
		exists: true,
		fetchedAt: dirStat.mtime,
		fileCount,
		isStale,
		indexed: false,
	};
}

// ============================================================================
// DOCPULL RUNNER
// ============================================================================

const inFlightFetches = new Map<
	string,
	Promise<{ success: boolean; message: string }>
>();

async function runDocpull(
	url: string,
	outputDir: string,
	maxPages?: number,
): Promise<{ success: boolean; message: string }> {
	return new Promise((resolve) => {
		const args = [url, "-o", outputDir, "--cache", "--profile", "rag"];
		if (maxPages) args.push("--max-pages", String(maxPages));
		const proc = spawn("docpull", args, { stdio: ["ignore", "pipe", "pipe"] });

		let stderr = "";
		let resolved = false;

		const timeout = setTimeout(() => {
			if (!resolved) {
				resolved = true;
				proc.kill("SIGTERM");
				resolve({ success: false, message: "Timeout after 10 minutes" });
			}
		}, DOCPULL_TIMEOUT_MS);

		proc.stderr.on("data", (d) => {
			stderr += d;
			if (stderr.length > 10000) {
				stderr = stderr.slice(-10000);
			}
		});

		proc.on("close", (code) => {
			if (!resolved) {
				resolved = true;
				clearTimeout(timeout);
				resolve(
					code === 0
						? { success: true, message: "Done" }
						: { success: false, message: stderr || "failed" },
				);
			}
		});

		proc.on("error", (e) => {
			if (!resolved) {
				resolved = true;
				clearTimeout(timeout);
				resolve({
					success: false,
					message: "Is docpull installed? " + e.message,
				});
			}
		});
	});
}

// ============================================================================
// MCP SERVER
// ============================================================================

const server = new McpServer({ name: "docpull-mcp", version: "0.2.0" });

// ---------------------------------------------------------------------------
// ensure_docs - fetch and optionally index documentation
// ---------------------------------------------------------------------------

server.tool(
	"ensure_docs",
	"Fetch documentation for a library. Optionally indexes for semantic search.",
	{
		source: z.string().describe("Library name or URL"),
		force: z.boolean().optional().default(false).describe("Force re-fetch"),
		index: z
			.boolean()
			.optional()
			.default(true)
			.describe(
				"Index for semantic search (requires DATABASE_URL and OPENAI_API_KEY)",
			),
	},
	async ({ source, force, index }) => {
		const sources = getAllSources();
		let url: string, name: string, maxPages: number | undefined;

		if (source.startsWith("http")) {
			url = source;
			name = new URL(source).hostname.replace(/\./g, "-");
		} else {
			const cfg = sources[source];
			if (!cfg)
				return {
					content: [{ type: "text" as const, text: "Unknown: " + source }],
					isError: true,
				};
			url = cfg.url;
			name = source;
			maxPages = cfg.maxPages;
		}

		const cache = await getCacheInfo(name);
		const needsFetch = !cache.exists || cache.isStale || force;
		const needsIndex = index && isDbConfigured() && openai;

		// Return early if cached and indexed
		if (!needsFetch && cache.exists && (!needsIndex || cache.indexed)) {
			return {
				content: [
					{
						type: "text" as const,
						text:
							source +
							" cached (" +
							cache.fileCount +
							" files" +
							(cache.indexed ? ", indexed" : "") +
							")",
					},
				],
			};
		}

		// Fetch if needed
		if (needsFetch) {
			const existing = inFlightFetches.get(name);
			if (existing) {
				const result = await existing;
				if (!result.success) {
					return {
						content: [{ type: "text" as const, text: result.message }],
						isError: true,
					};
				}
			} else {
				const fetchPromise = runDocpull(url, join(DOCS_DIR, name), maxPages);
				inFlightFetches.set(name, fetchPromise);

				try {
					const result = await fetchPromise;
					if (!result.success) {
						return {
							content: [{ type: "text" as const, text: result.message }],
							isError: true,
						};
					}
				} finally {
					inFlightFetches.delete(name);
				}
			}
		}

		const fileCount = await countMarkdownFiles(join(DOCS_DIR, name));
		let indexed = cache.exists ? cache.indexed : false;

		// Index if requested and configured
		if (needsIndex && (!cache.exists || !cache.indexed || needsFetch)) {
			try {
				await ingestSingleLibrary(name);
				indexed = true;
			} catch (e) {
				const msg = e instanceof Error ? e.message : String(e);
				return {
					content: [
						{
							type: "text" as const,
							text: `Fetched ${source} (${fileCount} files) but indexing failed: ${msg}`,
						},
					],
					isError: true,
				};
			}
		}

		writeMeta(name, { fetchedAt: Date.now(), fileCount, indexed });

		return {
			content: [
				{
					type: "text" as const,
					text:
						"Fetched " +
						source +
						" (" +
						fileCount +
						" files" +
						(indexed ? ", indexed" : "") +
						")",
				},
			],
		};
	},
);

// ---------------------------------------------------------------------------
// list_sources - list available documentation sources
// ---------------------------------------------------------------------------

server.tool(
	"list_sources",
	"List available documentation sources",
	{
		category: z
			.string()
			.optional()
			.describe("Filter: frontend, backend, ai, database"),
	},
	async ({ category }) => {
		let entries = Object.entries(getAllSources());
		if (category) entries = entries.filter(([, c]) => c.category === category);

		const results = await Promise.all(
			entries.map(async ([n, c]) => {
				const cache = await getCacheInfo(n);
				let status: string;
				if (!cache.exists) {
					status = "not fetched";
				} else if (cache.isStale) {
					status = "stale";
				} else {
					status = cache.fileCount + " files";
					if (cache.indexed) status += ", indexed";
				}
				return "- " + n + ": " + c.description + " (" + status + ")";
			}),
		);

		return { content: [{ type: "text" as const, text: results.join("\n") }] };
	},
);

// ---------------------------------------------------------------------------
// search_docs - semantic search (requires DB + OpenAI)
// ---------------------------------------------------------------------------

if (isDbConfigured() && openai) {
	server.tool(
		"search_docs",
		`Semantic search for CONCEPTS - use when you don't know the exact name.

Use grep_docs instead if you're looking for a specific method/function name.

Examples:
  - "how to stream responses"
  - "row level security"
  - "make object properties optional"`,
		{
			query: z
				.string()
				.min(2)
				.max(500)
				.describe("Natural language search query"),
			library: z.string().optional().describe("Filter to specific library"),
			limit: z
				.number()
				.int()
				.min(1)
				.max(50)
				.default(5)
				.describe("Max results (default: 5)"),
		},
		async ({ query, library, limit }) => {
			try {
				const response = await openai.embeddings.create({
					model: "text-embedding-3-small",
					input: query,
				});
				const queryEmbedding = response.data[0]?.embedding;
				if (!queryEmbedding) {
					throw new Error("Failed to generate embedding");
				}

				const results = await searchDocs(queryEmbedding, { library, limit });

				if (results.length === 0) {
					return {
						content: [
							{
								type: "text" as const,
								text: "No results found for: " + query,
							},
						],
					};
				}

				const output = results
					.map(
						(r) =>
							`## ${r.library} - ${r.file_path}\n*Similarity: ${(r.similarity * 100).toFixed(1)}%*\n\n${r.content}`,
					)
					.join("\n\n---\n\n");

				return { content: [{ type: "text" as const, text: output }] };
			} catch (e) {
				const msg = e instanceof Error ? e.message : String(e);
				return {
					content: [{ type: "text" as const, text: "Search failed: " + msg }],
					isError: true,
				};
			}
		},
	);

	// ---------------------------------------------------------------------------
	// grep_docs - exact pattern matching (requires DB)
	// ---------------------------------------------------------------------------

	server.tool(
		"grep_docs",
		`FAST exact text search - use for known method/function/component names.

Examples:
  - "onConflictDoUpdate"
  - "usePrefetchQuery"
  - "streamText"`,
		{
			pattern: z.string().min(2).max(200).describe("Exact text to search for"),
			library: z.string().optional().describe("Filter to specific library"),
			limit: z
				.number()
				.int()
				.min(1)
				.max(20)
				.default(5)
				.describe("Max results (default: 5)"),
		},
		async ({ pattern, library, limit }) => {
			try {
				const results = await grepDocs(pattern, { library, limit });

				if (results.length === 0) {
					return {
						content: [
							{
								type: "text" as const,
								text: "No matches for: " + pattern,
							},
						],
					};
				}

				const output = results
					.map((r) => {
						const lines = r.content.split("\n");
						const matchingLines = lines
							.map((line, idx) => ({ line, idx }))
							.filter(({ line }) => line.includes(pattern))
							.slice(0, 3)
							.map(({ line, idx }) => `  ${idx + 1}: ${line.trim()}`)
							.join("\n");
						return `## ${r.library} - ${r.file_path}\n${matchingLines}`;
					})
					.join("\n\n");

				return { content: [{ type: "text" as const, text: output }] };
			} catch (e) {
				const msg = e instanceof Error ? e.message : String(e);
				return {
					content: [{ type: "text" as const, text: "Grep failed: " + msg }],
					isError: true,
				};
			}
		},
	);

	// ---------------------------------------------------------------------------
	// list_indexed - list indexed libraries in the database
	// ---------------------------------------------------------------------------

	server.tool(
		"list_indexed",
		"List all indexed documentation libraries with chunk counts",
		{},
		async () => {
			try {
				const libraries = await listLibraries();

				if (libraries.length === 0) {
					return {
						content: [
							{
								type: "text" as const,
								text: "No libraries indexed. Use ensure_docs to fetch and index documentation.",
							},
						],
					};
				}

				const lines = libraries.map(
					(l) => `- ${l.library}: ${l.chunks} chunks`,
				);
				return {
					content: [{ type: "text" as const, text: lines.join("\n") }],
				};
			} catch (e) {
				const msg = e instanceof Error ? e.message : String(e);
				return {
					content: [{ type: "text" as const, text: "Failed to list: " + msg }],
					isError: true,
				};
			}
		},
	);
}

// ---------------------------------------------------------------------------
// START SERVER
// ---------------------------------------------------------------------------

server.connect(new StdioServerTransport());

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
	renameSync,
} from "fs";
import { readdir, stat } from "fs/promises";
import { join } from "path";
import { homedir } from "os";
import { parse as parseYaml } from "yaml";
import { isDbConfigured, searchDocs, grepDocs, listLibraries } from "./db.js";
import { createEmbeddings, getConfiguredOpenAIClient } from "./embeddings.js";
import { errorMessage, logStructured } from "./logger.js";
import { ingestSingleLibrary } from "./ingest.js";
import {
	normalizeSourceConfig,
	resolveConfiguredSource,
	type SourceConfig,
} from "./source_resolver.js";
import { ensureDocsToolSchema } from "./tool_schemas.js";

// ============================================================================
// CONFIG
// ============================================================================

const DOCS_DIR =
	process.env.DOCS_DIR ||
	join(homedir(), ".local", "share", "docpull-mcp", "docs");
const CONFIG_DIR = join(homedir(), ".config", "docpull-mcp");
const META_DIR = join(CONFIG_DIR, "meta");
const SOURCES_CONFIG_PATH = join(CONFIG_DIR, "sources.yaml");
const CACHE_TTL_DAYS = 7;
const MS_PER_DAY = 86_400_000;
const DOCPULL_TIMEOUT_MS = 10 * 60 * 1_000;
const DOCPULL_KILL_GRACE_MS = 5_000;
const MAX_DOCPULL_STDERR_BYTES = 10_000;

const openai = getConfiguredOpenAIClient();

// ============================================================================
// SOURCE CONFIG
// ============================================================================

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

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function loadUserSources(): Record<string, SourceConfig> {
	if (!existsSync(SOURCES_CONFIG_PATH)) {
		userSourcesCache = {};
		return {};
	}
	try {
		const mtime = statSync(SOURCES_CONFIG_PATH).mtime.getTime();
		if (userSourcesCache && mtime === userSourcesMtime) {
			return userSourcesCache;
		}
		const parsed = parseYaml(readFileSync(SOURCES_CONFIG_PATH, "utf-8"));
		if (!isRecord(parsed)) {
			logStructured("warn", "Ignoring sources.yaml: top-level value must be a mapping", {
				path: SOURCES_CONFIG_PATH,
			});
			userSourcesCache = {};
			userSourcesMtime = mtime;
			return userSourcesCache;
		}
		const rawSources = parsed.sources;
		if (rawSources === undefined || rawSources === null) {
			userSourcesCache = {};
			userSourcesMtime = mtime;
			return userSourcesCache;
		}
		if (!isRecord(rawSources)) {
			logStructured("warn", "Ignoring sources.yaml: sources must be a mapping", {
				path: SOURCES_CONFIG_PATH,
			});
			userSourcesCache = {};
			userSourcesMtime = mtime;
			return userSourcesCache;
		}
		const normalized: Record<string, SourceConfig> = {};
		for (const [name, value] of Object.entries(rawSources)) {
			const result = normalizeSourceConfig(name, value);
			if (result.ok) {
				normalized[name] = result.config;
			} else {
				logStructured("warn", "Ignoring invalid source config", {
					source: name,
					reason: result.message,
					path: SOURCES_CONFIG_PATH,
				});
			}
		}
		userSourcesCache = normalized;
		userSourcesMtime = mtime;
		return userSourcesCache;
	} catch (error) {
		logStructured("error", "Failed to parse sources.yaml", {
			path: SOURCES_CONFIG_PATH,
			error: errorMessage(error),
		});
		return userSourcesCache || {};
	}
}

function getAllSources(): Record<string, SourceConfig> {
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
	if (!existsSync(p)) {
		return null;
	}
	try {
		const parsed = JSON.parse(readFileSync(p, "utf-8"));
		if (!isRecord(parsed)) {
			logStructured("warn", "Ignoring malformed fetch metadata", {
				source,
				path: p,
				reason: "metadata root is not an object",
			});
			return null;
		}
		if (
			typeof parsed.fetchedAt !== "number" ||
			typeof parsed.fileCount !== "number" ||
			(parsed.indexed !== undefined && typeof parsed.indexed !== "boolean")
		) {
			logStructured("warn", "Ignoring malformed fetch metadata", {
				source,
				path: p,
				reason: "metadata fields have invalid types",
			});
			return null;
		}
		return {
			fetchedAt: parsed.fetchedAt,
			fileCount: parsed.fileCount,
			indexed: parsed.indexed,
		};
	} catch (error) {
		logStructured("warn", "Could not read fetch metadata", {
			source,
			path: p,
			error: errorMessage(error),
		});
		return null;
	}
}

function writeMeta(source: string, meta: FetchMeta) {
	mkdirSync(META_DIR, { recursive: true });
	const path = getMetaPath(source);
	const tmp = `${path}.tmp`;
	writeFileSync(tmp, JSON.stringify(meta));
	renameSync(tmp, path);
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
		const isStale = Date.now() - meta.fetchedAt > CACHE_TTL_DAYS * MS_PER_DAY;
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
		Date.now() - dirStat.mtime.getTime() > CACHE_TTL_DAYS * MS_PER_DAY;
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
		if (maxPages) {
			args.push("--max-pages", String(maxPages));
		}
		const proc = spawn("docpull", args, { stdio: ["ignore", "ignore", "pipe"] });

		let stderr = "";
		let resolved = false;
		let forceKillTimer: NodeJS.Timeout | null = null;

		const finish = (
			success: boolean,
			message: string,
			clearForceKill = true,
		): void => {
			if (resolved) {
				return;
			}
			resolved = true;
			clearTimeout(timeout);
			if (clearForceKill && forceKillTimer) {
				clearTimeout(forceKillTimer);
			}
			resolve({ success, message });
		};

		const timeout = setTimeout(() => {
			proc.kill("SIGTERM");
			forceKillTimer = setTimeout(() => {
				proc.kill("SIGKILL");
			}, DOCPULL_KILL_GRACE_MS);
			finish(false, "Timeout after 10 minutes", false);
		}, DOCPULL_TIMEOUT_MS);

		proc.stderr.on("data", (d) => {
			stderr += String(d);
			if (stderr.length > MAX_DOCPULL_STDERR_BYTES) {
				stderr = stderr.slice(-MAX_DOCPULL_STDERR_BYTES);
			}
		});

		proc.on("close", (code) => {
			finish(code === 0, code === 0 ? "Done" : stderr || "failed");
		});

		proc.on("error", (error) => {
			finish(false, "Is docpull installed? " + errorMessage(error));
		});
	});
}

// ============================================================================
// MCP SERVER
// ============================================================================

const server = new McpServer({ name: "docpull-mcp", version: "0.3.0" });

// ---------------------------------------------------------------------------
// ensure_docs - fetch and optionally index documentation
// ---------------------------------------------------------------------------

server.tool(
	"ensure_docs",
	"Fetch documentation for a configured library. Optionally indexes for semantic search.",
	ensureDocsToolSchema,
	async ({ source, force, index }) => {
		const sources = getAllSources();
		const resolved = resolveConfiguredSource(
			source,
			sources,
			SOURCES_CONFIG_PATH,
		);

		if (!resolved.ok) {
			return {
				content: [{ type: "text" as const, text: resolved.message }],
				isError: true,
			};
		}

		const {
			name,
			url,
			maxPages,
		} = resolved.value;

		const cache = await getCacheInfo(name);
		const needsFetch = !cache.exists || cache.isStale || force;
		if (index && !isDbConfigured()) {
			return {
				content: [
					{
						type: "text" as const,
						text: "Indexing requested but DATABASE_URL is not configured.",
					},
				],
				isError: true,
			};
		}
		if (index && !openai) {
			return {
				content: [
					{
						type: "text" as const,
						text: "Indexing requested but OPENAI_API_KEY is not configured.",
					},
				],
				isError: true,
			};
		}
		const needsIndex = index && isDbConfigured() && openai !== null;

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
			} catch (error) {
				const msg = errorMessage(error);
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
				const [queryEmbedding] = await createEmbeddings(openai, query);
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
			} catch (error) {
				const msg = errorMessage(error);
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
						const patternLower = pattern.toLowerCase();
						const matchingLines = lines
							.map((line, idx) => ({ line, idx }))
							.filter(({ line }) => line.toLowerCase().includes(patternLower))
							.slice(0, 3)
							.map(({ line, idx }) => `  ${idx + 1}: ${line.trim()}`)
							.join("\n");
						return `## ${r.library} - ${r.file_path}\n${matchingLines}`;
					})
					.join("\n\n");

				return { content: [{ type: "text" as const, text: output }] };
			} catch (error) {
				const msg = errorMessage(error);
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
			} catch (error) {
				const msg = errorMessage(error);
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

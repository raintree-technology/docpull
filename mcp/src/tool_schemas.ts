import { z } from "zod";

export const DEFAULT_ENSURE_DOCS_INDEX = false;

export const ensureDocsToolSchema = {
	source: z.string().describe("Configured source name from list_sources"),
	force: z.boolean().optional().default(false).describe("Force re-fetch"),
	index: z
		.boolean()
		.optional()
		.default(DEFAULT_ENSURE_DOCS_INDEX)
		.describe(
			"Index for semantic search (requires DATABASE_URL and OPENAI_API_KEY). Defaults to false so third-party embedding is explicit opt-in.",
		),
};

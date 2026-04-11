import { describe, expect, test } from "bun:test";
import { z } from "zod";
import {
	DEFAULT_ENSURE_DOCS_INDEX,
	ensureDocsToolSchema,
} from "./tool_schemas.js";

describe("ensureDocsToolSchema", () => {
	test("keeps indexing opt-in by default", () => {
		const parsed = z.object(ensureDocsToolSchema).parse({ source: "react" });

		expect(DEFAULT_ENSURE_DOCS_INDEX).toBe(false);
		expect(parsed.index).toBe(false);
		expect(parsed.force).toBe(false);
	});
});

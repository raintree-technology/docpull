import { describe, expect, test } from "bun:test";
import { chunkText } from "./ingest.js";

describe("chunkText", () => {
	test("drops empty chunks for empty files", () => {
		expect(chunkText("", 1000, 200)).toEqual([]);
	});

	test("drops whitespace-only chunks", () => {
		expect(chunkText("  \n\t\n", 1000, 200)).toEqual([]);
	});

	test("preserves non-empty content", () => {
		expect(chunkText("# Heading\n\nBody", 1000, 200)).toEqual([
			"# Heading\n\nBody",
		]);
	});
});

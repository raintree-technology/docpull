import { describe, expect, test } from "bun:test";
import { resolveConfiguredSource, type SourceConfig } from "./source_resolver.js";

const CONFIG_PATH = "/tmp/sources.yaml";
const SOURCES: Record<string, SourceConfig> = {
	react: {
		url: "https://react.dev",
		description: "React documentation",
		category: "frontend",
		maxPages: 500,
	},
};

describe("resolveConfiguredSource", () => {
	test("rejects direct URL inputs with a config hint", () => {
		const result = resolveConfiguredSource(
			"https://docs.example.com",
			SOURCES,
			CONFIG_PATH,
		);

		expect(result).toEqual({
			ok: false,
			message:
				"Direct URLs are disabled for ensure_docs. Add an alias in /tmp/sources.yaml and call ensure_docs with that source name.",
		});
	});

	test("rejects unknown aliases", () => {
		const result = resolveConfiguredSource("unknown-lib", SOURCES, CONFIG_PATH);

		expect(result).toEqual({
			ok: false,
			message:
				"Unknown source: unknown-lib. Use list_sources or add it to /tmp/sources.yaml.",
		});
	});

	test("resolves configured sources", () => {
		const result = resolveConfiguredSource("react", SOURCES, CONFIG_PATH);

		expect(result).toEqual({
			ok: true,
			value: {
				name: "react",
				url: "https://react.dev",
				maxPages: 500,
			},
		});
	});
});

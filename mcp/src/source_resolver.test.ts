import { describe, expect, test } from "bun:test";
import {
	normalizeSourceConfig,
	resolveConfiguredSource,
	type SourceConfig,
} from "./source_resolver.js";

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

	test("rejects unsafe source names", () => {
		const result = resolveConfiguredSource("../react", SOURCES, CONFIG_PATH);

		expect(result).toEqual({
			ok: false,
			message: "Invalid source name: ../react. Use alnum plus _, ., or - with no leading dot.",
		});
	});

	test("rejects configured local and private URLs", () => {
		for (const url of [
			"https://localhost",
			"https://service.local/docs",
			"https://127.0.0.1/docs",
			"https://10.1.2.3/docs",
			"https://[::1]/docs",
			"https://[fc00::1]/docs",
		]) {
			expect(
				normalizeSourceConfig("unsafe", {
					url,
					description: "Unsafe",
					category: "test",
				}),
			).toEqual({
				ok: false,
				message: "Source 'unsafe' url must use HTTPS and a public host.",
			});
		}
	});

	test("rejects trailing-dot and DNS-rebinding host bypasses", () => {
		for (const url of [
			"https://localhost./docs", // trailing root dot evades === "localhost"
			"https://service.internal./docs", // trailing dot evades suffix check
			"https://169.254.169.254.nip.io/latest/meta-data/", // wildcard rebinding
			"https://10.0.0.1.sslip.io/admin",
			"https://127.0.0.1.xip.io/",
			"https://user:pass@docs.example.com/private",
		]) {
			expect(
				normalizeSourceConfig("unsafe", {
					url,
					description: "Unsafe",
					category: "test",
				}),
			).toEqual({
				ok: false,
				message: "Source 'unsafe' url must use HTTPS and a public host.",
			});
		}
	});

	test("rejects invalid maxPages values", () => {
		const result = normalizeSourceConfig("react", {
			url: "https://react.dev",
			maxPages: 0,
		});

		expect(result).toEqual({
			ok: false,
			message: "source 'react' maxPages must be between 1 and 100000",
		});
	});
});

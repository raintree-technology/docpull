import { afterEach, describe, expect, test } from "bun:test";
import { readIntegerEnv } from "./env.js";

const TEST_ENV = "DOCPULL_MCP_TEST_INTEGER";

afterEach(() => {
	delete process.env[TEST_ENV];
});

describe("readIntegerEnv", () => {
	test("rejects partially numeric values", () => {
		process.env[TEST_ENV] = "10abc";

		expect(() =>
			readIntegerEnv(TEST_ENV, 5, { min: 1, max: 20 }),
		).toThrow("DOCPULL_MCP_TEST_INTEGER must be an integer between 1 and 20");
	});

	test("accepts valid integer values", () => {
		process.env[TEST_ENV] = "10";

		expect(readIntegerEnv(TEST_ENV, 5, { min: 1, max: 20 })).toBe(10);
	});
});

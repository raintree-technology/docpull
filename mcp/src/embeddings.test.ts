import { afterEach, describe, expect, test } from "bun:test";
import {
	getConfiguredOpenAIClient,
	requireConfiguredOpenAIClient,
} from "./embeddings.js";

const TEST_OPENAI_API_KEY_ENV = "OPENAI_API_KEY";

afterEach(() => {
	delete process.env[TEST_OPENAI_API_KEY_ENV];
});

describe("OpenAI client configuration", () => {
	test("reads OPENAI_API_KEY at call time", () => {
		delete process.env[TEST_OPENAI_API_KEY_ENV];
		expect(getConfiguredOpenAIClient()).toBeNull();

		process.env[TEST_OPENAI_API_KEY_ENV] = "sk-test-123456789012";
		expect(getConfiguredOpenAIClient()).not.toBeNull();
	});

	test("throws when OPENAI_API_KEY is missing", () => {
		delete process.env[TEST_OPENAI_API_KEY_ENV];

		expect(() => requireConfiguredOpenAIClient()).toThrow(
			"OPENAI_API_KEY environment variable required",
		);
	});
});

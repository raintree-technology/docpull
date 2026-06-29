import { afterEach, describe, expect, test } from "bun:test";
import type { OpenAI } from "openai";
import {
	EMBEDDING_DIMENSIONS,
	createEmbeddings,
	estimateEmbeddingInputTokens,
	resetEmbeddingQuotaForTests,
} from "./embeddings.js";

function fakeClient(): { client: OpenAI; calls: { input: string | string[] }[] } {
	const calls: { input: string | string[] }[] = [];
	const client = {
		embeddings: {
			async create({ input }: { input: string | string[] }) {
				calls.push({ input });
				const inputs = Array.isArray(input) ? input : [input];
				return {
					data: inputs.map(() => ({
						embedding: Array.from({ length: EMBEDDING_DIMENSIONS }, () => 0.001),
					})),
				};
			},
		},
	} as unknown as OpenAI;
	return { client, calls };
}

describe("createEmbeddings quota guard", () => {
	afterEach(() => {
		delete process.env.DOCPULL_MCP_EMBEDDING_MAX_INPUT_TOKENS;
		resetEmbeddingQuotaForTests();
	});

	test("fails closed when no embedding token quota is configured", async () => {
		const { client, calls } = fakeClient();

		await expect(createEmbeddings(client, "hello")).rejects.toThrow(
			"OpenAI embedding quota is not configured",
		);

		expect(calls).toEqual([]);
	});

	test("debits estimated input tokens before paid embedding calls", async () => {
		process.env.DOCPULL_MCP_EMBEDDING_MAX_INPUT_TOKENS = "3";
		const { client, calls } = fakeClient();

		await createEmbeddings(client, "abcd");
		await createEmbeddings(client, "efgh");
		await createEmbeddings(client, "i");
		await expect(createEmbeddings(client, "j")).rejects.toThrow(
			"OpenAI embedding quota exceeded",
		);

		expect(calls.length).toBe(3);
	});

	test("estimates batches cumulatively", () => {
		expect(estimateEmbeddingInputTokens(["abcd", "abcdefgh"])).toBe(3);
	});
});

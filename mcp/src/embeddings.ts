import { OpenAI } from "openai";
import { readIntegerEnv } from "./env.js";
import { errorMessage, logStructured } from "./logger.js";

const EMBEDDING_MODEL = "text-embedding-3-small";
export const EMBEDDING_DIMENSIONS = 1536;

const DEFAULT_OPENAI_TIMEOUT_MS = 30_000;
const DEFAULT_OPENAI_MAX_RETRIES = 2;
const DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5;
const DEFAULT_CIRCUIT_RESET_MS = 60_000;
const DEFAULT_EMBEDDING_MAX_INPUT_TOKENS = 0;
const MAX_EMBEDDING_INPUT_TOKENS = 100_000_000;

const OPENAI_TIMEOUT_MS = readIntegerEnv(
	"OPENAI_TIMEOUT_MS",
	DEFAULT_OPENAI_TIMEOUT_MS,
	{ min: 1_000, max: 300_000 },
);
const OPENAI_MAX_RETRIES = readIntegerEnv(
	"OPENAI_MAX_RETRIES",
	DEFAULT_OPENAI_MAX_RETRIES,
	{ min: 0, max: 10 },
);
const CIRCUIT_FAILURE_THRESHOLD = readIntegerEnv(
	"OPENAI_CIRCUIT_FAILURE_THRESHOLD",
	DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
	{ min: 1, max: 100 },
);
const CIRCUIT_RESET_MS = readIntegerEnv(
	"OPENAI_CIRCUIT_RESET_MS",
	DEFAULT_CIRCUIT_RESET_MS,
	{ min: 1_000, max: 3_600_000 },
);

let embeddingInputTokensUsed = 0;

class CircuitBreaker {
	private failures = 0;
	private openedAt: number | null = null;

	beforeRequest(): void {
		if (this.openedAt === null) {
			return;
		}
		const elapsedMs = Date.now() - this.openedAt;
		if (elapsedMs >= CIRCUIT_RESET_MS) {
			this.openedAt = null;
			this.failures = 0;
			return;
		}
		throw new Error(
			`OpenAI circuit is open; retry after ${Math.ceil((CIRCUIT_RESET_MS - elapsedMs) / 1000)}s`,
		);
	}

	recordSuccess(): void {
		this.failures = 0;
		this.openedAt = null;
	}

	recordFailure(error: unknown): void {
		this.failures += 1;
		if (this.failures >= CIRCUIT_FAILURE_THRESHOLD && this.openedAt === null) {
			this.openedAt = Date.now();
			logStructured("error", "OpenAI circuit opened", {
				failures: this.failures,
				error: errorMessage(error),
			});
		}
	}
}

const embeddingCircuit = new CircuitBreaker();

function createOpenAIClient(apiKey: string): OpenAI {
	return new OpenAI({
		apiKey,
		timeout: OPENAI_TIMEOUT_MS,
		maxRetries: OPENAI_MAX_RETRIES,
	});
}

export function getConfiguredOpenAIClient(): OpenAI | null {
	const key = process.env.OPENAI_API_KEY;
	return key ? createOpenAIClient(key) : null;
}

export function requireConfiguredOpenAIClient(): OpenAI {
	const client = getConfiguredOpenAIClient();
	if (client === null) {
		throw new Error("OPENAI_API_KEY environment variable required");
	}
	return client;
}

export function estimateEmbeddingInputTokens(input: string | string[]): number {
	const values = Array.isArray(input) ? input : [input];
	return values.reduce((total, text) => total + Math.max(1, Math.ceil(text.length / 4)), 0);
}

export function resetEmbeddingQuotaForTests(): void {
	embeddingInputTokensUsed = 0;
}

function readEmbeddingMaxInputTokens(): number {
	return readIntegerEnv(
		"DOCPULL_MCP_EMBEDDING_MAX_INPUT_TOKENS",
		DEFAULT_EMBEDDING_MAX_INPUT_TOKENS,
		{ min: 0, max: MAX_EMBEDDING_INPUT_TOKENS },
	);
}

function reserveEmbeddingQuota(input: string | string[]): void {
	const maxInputTokens = readEmbeddingMaxInputTokens();
	if (maxInputTokens <= 0) {
		throw new Error(
			"OpenAI embedding quota is not configured; set DOCPULL_MCP_EMBEDDING_MAX_INPUT_TOKENS to a positive token budget",
		);
	}
	const estimatedTokens = estimateEmbeddingInputTokens(input);
	if (embeddingInputTokensUsed + estimatedTokens > maxInputTokens) {
		throw new Error(
			`OpenAI embedding quota exceeded: ${embeddingInputTokensUsed + estimatedTokens}/${maxInputTokens} estimated input tokens`,
		);
	}
	embeddingInputTokensUsed += estimatedTokens;
}

export async function createEmbeddings(
	client: OpenAI,
	input: string | string[],
): Promise<number[][]> {
	embeddingCircuit.beforeRequest();
	reserveEmbeddingQuota(input);
	try {
		const response = await client.embeddings.create(
			{
				model: EMBEDDING_MODEL,
				input,
			},
			{
				timeout: OPENAI_TIMEOUT_MS,
				maxRetries: OPENAI_MAX_RETRIES,
			},
		);
		const embeddings = response.data.map((item) => item.embedding);
		for (const embedding of embeddings) {
			if (embedding.length !== EMBEDDING_DIMENSIONS) {
				throw new Error(
					`Embedding dimension mismatch: expected ${EMBEDDING_DIMENSIONS}, got ${embedding.length}`,
				);
			}
		}
		embeddingCircuit.recordSuccess();
		return embeddings;
	} catch (error) {
		embeddingCircuit.recordFailure(error);
		throw error;
	}
}

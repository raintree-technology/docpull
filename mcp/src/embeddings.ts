import { OpenAI } from "openai";
import { readIntegerEnv } from "./env.js";
import { errorMessage, logStructured } from "./logger.js";

const EMBEDDING_MODEL = "text-embedding-3-small";
export const EMBEDDING_DIMENSIONS = 1536;

const DEFAULT_OPENAI_TIMEOUT_MS = 30_000;
const DEFAULT_OPENAI_MAX_RETRIES = 2;
const DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5;
const DEFAULT_CIRCUIT_RESET_MS = 60_000;

function getOpenAITimeoutMs(): number {
	return readIntegerEnv("OPENAI_TIMEOUT_MS", DEFAULT_OPENAI_TIMEOUT_MS, {
		min: 1_000,
		max: 300_000,
	});
}

function getOpenAIMaxRetries(): number {
	return readIntegerEnv("OPENAI_MAX_RETRIES", DEFAULT_OPENAI_MAX_RETRIES, {
		min: 0,
		max: 10,
	});
}

function getCircuitFailureThreshold(): number {
	return readIntegerEnv(
		"OPENAI_CIRCUIT_FAILURE_THRESHOLD",
		DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
		{ min: 1, max: 100 },
	);
}

function getCircuitResetMs(): number {
	return readIntegerEnv("OPENAI_CIRCUIT_RESET_MS", DEFAULT_CIRCUIT_RESET_MS, {
		min: 1_000,
		max: 3_600_000,
	});
}

class CircuitBreaker {
	private failures = 0;
	private openedAt: number | null = null;

	beforeRequest(): void {
		if (this.openedAt === null) {
			return;
		}
		const circuitResetMs = getCircuitResetMs();
		const elapsedMs = Date.now() - this.openedAt;
		if (elapsedMs >= circuitResetMs) {
			this.openedAt = null;
			this.failures = 0;
			return;
		}
		throw new Error(
			`OpenAI circuit is open; retry after ${Math.ceil((circuitResetMs - elapsedMs) / 1000)}s`,
		);
	}

	recordSuccess(): void {
		this.failures = 0;
		this.openedAt = null;
	}

	recordFailure(error: unknown): void {
		this.failures += 1;
		if (
			this.failures >= getCircuitFailureThreshold() &&
			this.openedAt === null
		) {
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
		timeout: getOpenAITimeoutMs(),
		maxRetries: getOpenAIMaxRetries(),
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

export async function createEmbeddings(
	client: OpenAI,
	input: string | string[],
): Promise<number[][]> {
	embeddingCircuit.beforeRequest();
	try {
		const openAITimeoutMs = getOpenAITimeoutMs();
		const openAIMaxRetries = getOpenAIMaxRetries();
		const response = await client.embeddings.create(
			{
				model: EMBEDDING_MODEL,
				input,
			},
			{
				timeout: openAITimeoutMs,
				maxRetries: openAIMaxRetries,
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

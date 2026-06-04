import { describe, expect, test } from "bun:test";
import {
	type DbClient,
	type EmbeddingDocument,
	replaceLibraryEmbeddingsWithClient,
} from "./db.js";
import { EMBEDDING_DIMENSIONS } from "./embeddings.js";

interface QueryRecord {
	sql: string;
	params?: readonly unknown[];
}

class FakeClient implements DbClient {
	readonly queries: QueryRecord[] = [];

	constructor(private readonly failOnSql?: string) {}

	async query(
		sql: string,
		params?: readonly unknown[],
	): Promise<{ rowCount: number | null; rows: Array<Record<string, unknown>> }> {
		this.queries.push({ sql, params });
		if (this.failOnSql && sql.includes(this.failOnSql)) {
			throw new Error(`forced failure on ${this.failOnSql}`);
		}
		return { rowCount: 0, rows: [] };
	}
}

function embedding(): number[] {
	return Array.from({ length: EMBEDDING_DIMENSIONS }, () => 0.001);
}

function doc(overrides: Partial<EmbeddingDocument> = {}): EmbeddingDocument {
	return {
		library: "react",
		file_path: "index.md",
		chunk_index: 0,
		content: "content",
		embedding: embedding(),
		metadata: { heading: "Intro" },
		...overrides,
	};
}

describe("replaceLibraryEmbeddingsWithClient", () => {
	test("deletes and reinserts one library inside a single transaction", async () => {
		const client = new FakeClient();

		await replaceLibraryEmbeddingsWithClient(client, "react", [doc()]);

		expect(client.queries.map((query) => query.sql)).toEqual([
			"BEGIN",
			"DELETE FROM doc_embeddings WHERE library = $1",
			expect.stringContaining("INSERT INTO doc_embeddings"),
			"COMMIT",
		]);
		expect(client.queries[1].params).toEqual(["react"]);
	});

	test("rolls back the delete when the replacement insert fails", async () => {
		const client = new FakeClient("INSERT INTO doc_embeddings");

		await expect(
			replaceLibraryEmbeddingsWithClient(client, "react", [doc()]),
		).rejects.toThrow("forced failure");

		expect(client.queries.map((query) => query.sql)).toEqual([
			"BEGIN",
			"DELETE FROM doc_embeddings WHERE library = $1",
			expect.stringContaining("INSERT INTO doc_embeddings"),
			"ROLLBACK",
		]);
	});

	test("rejects library mismatches before opening a transaction", async () => {
		const client = new FakeClient();

		await expect(
			replaceLibraryEmbeddingsWithClient(client, "react", [
				doc({ library: "nextjs" }),
			]),
		).rejects.toThrow("Embedding document library mismatch");

		expect(client.queries).toEqual([]);
	});

	test("splits inserts that exceed the postgres bind-parameter ceiling", async () => {
		const client = new FakeClient();
		// 5462 docs * 6 params = 32772 > 32767, so it must split into 2 inserts,
		// both still inside the single delete+insert transaction.
		const docs = Array.from({ length: 5462 }, (_, i) =>
			doc({ file_path: `f${i}.md`, chunk_index: i }),
		);

		await replaceLibraryEmbeddingsWithClient(client, "react", docs);

		const inserts = client.queries.filter((query) =>
			query.sql.includes("INSERT INTO doc_embeddings"),
		);
		expect(inserts.length).toBe(2);
		for (const insert of inserts) {
			expect((insert.params ?? []).length).toBeLessThanOrEqual(32767);
		}

		const sqls = client.queries.map((query) => query.sql);
		expect(sqls.filter((sql) => sql === "BEGIN").length).toBe(1);
		expect(sqls[sqls.length - 1]).toBe("COMMIT");
	});
});

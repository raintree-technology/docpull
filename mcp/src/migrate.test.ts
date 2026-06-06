import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, test } from "bun:test";
import {
	listMigrationFiles,
	migrateDatabase,
	migrationStatus,
	parseMigrationFilename,
	rollbackLatestMigration,
	setupDatabase,
} from "./migrate.js";

interface QueryRecord {
	sql: string;
	params?: readonly unknown[];
}

class FakeMigrationClient {
	readonly queries: QueryRecord[] = [];
	private readonly applied = new Set<string>();

	constructor(applied: string[] = []) {
		for (const id of applied) {
			this.applied.add(id);
		}
	}

	async query(
		sql: string,
		params?: readonly unknown[],
	): Promise<{ rows: Array<{ id: string }> }> {
		this.queries.push({ sql, params });
		if (sql.startsWith("SELECT id FROM docpull_mcp_migrations")) {
			return { rows: [...this.applied].sort().map((id) => ({ id })) };
		}
		if (sql.startsWith("INSERT INTO docpull_mcp_migrations")) {
			this.applied.add(String(params?.[0]));
		}
		if (sql.startsWith("DELETE FROM docpull_mcp_migrations")) {
			this.applied.delete(String(params?.[0]));
		}
		return { rows: [] };
	}
}

let tempDirs: string[] = [];

function tempMigrationDir(): string {
	const dir = mkdtempSync(join(tmpdir(), "docpull-mcp-migrations-"));
	tempDirs.push(dir);
	return dir;
}

function writeMigration(dir: string, filename: string, sql: string): void {
	writeFileSync(join(dir, filename), sql);
}

afterEach(() => {
	for (const dir of tempDirs) {
		rmSync(dir, { recursive: true, force: true });
	}
	tempDirs = [];
});

describe("migration filename discovery", () => {
	test("parses valid migration filenames", () => {
		expect(parseMigrationFilename("001_harden_embeddings.up.sql")).toMatchObject({
			id: "001_harden_embeddings",
			direction: "up",
		});
		expect(parseMigrationFilename("001_harden_embeddings.down.sql")).toMatchObject({
			id: "001_harden_embeddings",
			direction: "down",
		});
	});

	test("ignores non-migration files and sorts migrations", () => {
		const dir = tempMigrationDir();
		writeMigration(dir, "002_second.up.sql", "SELECT 2");
		writeMigration(dir, "README.md", "ignore");
		writeMigration(dir, "001_first.up.sql", "SELECT 1");

		expect(listMigrationFiles(dir).map((file) => file.filename)).toEqual([
			"001_first.up.sql",
			"002_second.up.sql",
		]);
	});
});

describe("migrateDatabase", () => {
	test("applies only pending up migrations and records them", async () => {
		const dir = tempMigrationDir();
		writeMigration(dir, "001_first.up.sql", "SELECT 1");
		writeMigration(dir, "002_second.up.sql", "SELECT 2");
		const client = new FakeMigrationClient(["001_first"]);

		const ran = await migrateDatabase(client, { migrationDir: dir });

		expect(ran).toEqual(["002_second"]);
		expect(client.queries.map((query) => query.sql)).toEqual([
			expect.stringContaining("CREATE TABLE IF NOT EXISTS docpull_mcp_migrations"),
			"SELECT id FROM docpull_mcp_migrations ORDER BY id",
			"BEGIN",
			"SELECT 2",
			"INSERT INTO docpull_mcp_migrations (id) VALUES ($1)",
			"COMMIT",
		]);
	});

	test("setup applies schema before pending migrations", async () => {
		const dir = tempMigrationDir();
		const schemaFile = join(dir, "schema.sql");
		writeFileSync(schemaFile, "CREATE EXTENSION vector");
		writeMigration(dir, "001_first.up.sql", "SELECT 1");
		const client = new FakeMigrationClient();

		const ran = await setupDatabase(client, { schemaFile, migrationDir: dir });

		expect(ran).toEqual(["001_first"]);
		expect(client.queries[0].sql).toBe("CREATE EXTENSION vector");
	});
});

describe("rollbackLatestMigration", () => {
	test("rolls back the latest applied migration", async () => {
		const dir = tempMigrationDir();
		writeMigration(dir, "001_first.down.sql", "SELECT 'down 1'");
		writeMigration(dir, "002_second.down.sql", "SELECT 'down 2'");
		const client = new FakeMigrationClient(["001_first", "002_second"]);

		const rolledBack = await rollbackLatestMigration(client, {
			migrationDir: dir,
		});

		expect(rolledBack).toBe("002_second");
		expect(client.queries.map((query) => query.sql)).toContain("SELECT 'down 2'");
		expect(client.queries.map((query) => query.sql)).toContain(
			"DELETE FROM docpull_mcp_migrations WHERE id = $1",
		);
	});

	test("returns null when nothing has been applied", async () => {
		const client = new FakeMigrationClient();

		await expect(rollbackLatestMigration(client)).resolves.toBeNull();
	});
});

describe("migrationStatus", () => {
	test("reports applied and pending migrations", async () => {
		const dir = tempMigrationDir();
		writeMigration(dir, "001_first.up.sql", "SELECT 1");
		writeMigration(dir, "002_second.up.sql", "SELECT 2");
		const client = new FakeMigrationClient(["001_first"]);

		await expect(migrationStatus(client, { migrationDir: dir })).resolves.toEqual([
			{
				id: "001_first",
				filename: "001_first.up.sql",
				applied: true,
			},
			{
				id: "002_second",
				filename: "002_second.up.sql",
				applied: false,
			},
		]);
	});
});

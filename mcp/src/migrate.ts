#!/usr/bin/env bun
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Pool } from "pg";
import { errorMessage } from "./logger.js";

type Direction = "up" | "down";

interface Queryable {
	query(sql: string, params?: readonly unknown[]): Promise<unknown>;
	end?(): Promise<void>;
}

export interface MigrationFile {
	id: string;
	direction: Direction;
	filename: string;
	path: string;
}

export interface MigrationStatus {
	id: string;
	filename: string;
	applied: boolean;
}

const MIGRATION_RE = /^([0-9]+_[a-z0-9_]+)\.(up|down)\.sql$/;
const MIGRATIONS_TABLE = "docpull_mcp_migrations";

function repoRoot(): string {
	return resolve(dirname(fileURLToPath(import.meta.url)), "..");
}

function migrationsDir(): string {
	return join(repoRoot(), "migrations");
}

function schemaPath(): string {
	return join(repoRoot(), "schema.sql");
}

function getDatabaseUrl(): string {
	const url = process.env.DATABASE_URL;
	if (!url) {
		throw new Error("DATABASE_URL environment variable is required");
	}
	return url;
}

export function parseMigrationFilename(
	filename: string,
	dir = migrationsDir(),
): MigrationFile | null {
	const match = MIGRATION_RE.exec(filename);
	if (!match) {
		return null;
	}
	return {
		id: match[1],
		direction: match[2] as Direction,
		filename,
		path: join(dir, filename),
	};
}

export function listMigrationFiles(dir = migrationsDir()): MigrationFile[] {
	if (!existsSync(dir)) {
		return [];
	}
	return readdirSync(dir)
		.map((filename) => parseMigrationFilename(filename, dir))
		.filter((file): file is MigrationFile => file !== null)
		.sort((a, b) => a.filename.localeCompare(b.filename));
}

function upMigrations(dir = migrationsDir()): MigrationFile[] {
	return listMigrationFiles(dir).filter((file) => file.direction === "up");
}

function downMigrationFor(id: string, dir = migrationsDir()): MigrationFile | null {
	return (
		listMigrationFiles(dir).find(
			(file) => file.id === id && file.direction === "down",
		) ?? null
	);
}

async function ensureMigrationsTable(client: Queryable): Promise<void> {
	await client.query(`
		CREATE TABLE IF NOT EXISTS ${MIGRATIONS_TABLE} (
			id TEXT PRIMARY KEY,
			applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		)
	`);
}

async function appliedMigrationIds(client: Queryable): Promise<Set<string>> {
	const result = await client.query(
		`SELECT id FROM ${MIGRATIONS_TABLE} ORDER BY id`,
	);
	const rows = (result as { rows?: Array<{ id: unknown }> }).rows ?? [];
	return new Set(rows.map((row) => String(row.id)));
}

async function applySqlFile(client: Queryable, path: string): Promise<void> {
	await client.query(readFileSync(path, "utf-8"));
}

export async function setupDatabase(
	client: Queryable,
	{
		schemaFile = schemaPath(),
		migrationDir = migrationsDir(),
	}: { schemaFile?: string; migrationDir?: string } = {},
): Promise<string[]> {
	await applySqlFile(client, schemaFile);
	return migrateDatabase(client, { migrationDir });
}

export async function migrateDatabase(
	client: Queryable,
	{ migrationDir = migrationsDir() }: { migrationDir?: string } = {},
): Promise<string[]> {
	await ensureMigrationsTable(client);
	const applied = await appliedMigrationIds(client);
	const ran: string[] = [];

	for (const migration of upMigrations(migrationDir)) {
		if (applied.has(migration.id)) {
			continue;
		}
		await client.query("BEGIN");
		try {
			await applySqlFile(client, migration.path);
			await client.query(
				`INSERT INTO ${MIGRATIONS_TABLE} (id) VALUES ($1)`,
				[migration.id],
			);
			await client.query("COMMIT");
			ran.push(migration.id);
		} catch (error) {
			await client.query("ROLLBACK");
			throw error;
		}
	}

	return ran;
}

export async function rollbackLatestMigration(
	client: Queryable,
	{ migrationDir = migrationsDir() }: { migrationDir?: string } = {},
): Promise<string | null> {
	await ensureMigrationsTable(client);
	const applied = [...(await appliedMigrationIds(client))].sort();
	const latest = applied.at(-1);
	if (!latest) {
		return null;
	}

	const migration = downMigrationFor(latest, migrationDir);
	if (!migration) {
		throw new Error(`No down migration found for ${latest}`);
	}

	await client.query("BEGIN");
	try {
		await applySqlFile(client, migration.path);
		await client.query(`DELETE FROM ${MIGRATIONS_TABLE} WHERE id = $1`, [
			latest,
		]);
		await client.query("COMMIT");
		return latest;
	} catch (error) {
		await client.query("ROLLBACK");
		throw error;
	}
}

export async function migrationStatus(
	client: Queryable,
	{ migrationDir = migrationsDir() }: { migrationDir?: string } = {},
): Promise<MigrationStatus[]> {
	await ensureMigrationsTable(client);
	const applied = await appliedMigrationIds(client);
	return upMigrations(migrationDir).map((migration) => ({
		id: migration.id,
		filename: migration.filename,
		applied: applied.has(migration.id),
	}));
}

async function withPool<T>(fn: (client: Queryable) => Promise<T>): Promise<T> {
	const pool = new Pool({ connectionString: getDatabaseUrl() });
	try {
		return await fn(pool);
	} finally {
		await pool.end();
	}
}

function printUsage(): void {
	process.stderr.write(`Usage: bun run src/migrate.ts <setup|migrate|rollback|status>

Commands:
  setup     Apply schema.sql, create migration tracking, then apply pending migrations
  migrate   Apply pending *.up.sql migrations
  rollback  Apply the latest applied *.down.sql migration
  status    Show applied and pending migrations
`);
}

async function main(): Promise<number> {
	const command = process.argv[2] ?? "status";
	try {
		if (command === "setup") {
			const ran = await withPool((client) => setupDatabase(client));
			process.stdout.write(
				ran.length === 0
					? "Database schema is ready; no pending migrations.\n"
					: `Database schema is ready; applied migrations: ${ran.join(", ")}\n`,
			);
			return 0;
		}
		if (command === "migrate") {
			const ran = await withPool((client) => migrateDatabase(client));
			process.stdout.write(
				ran.length === 0
					? "No pending migrations.\n"
					: `Applied migrations: ${ran.join(", ")}\n`,
			);
			return 0;
		}
		if (command === "rollback") {
			const rolledBack = await withPool((client) =>
				rollbackLatestMigration(client),
			);
			process.stdout.write(
				rolledBack
					? `Rolled back migration: ${rolledBack}\n`
					: "No applied migrations to roll back.\n",
			);
			return 0;
		}
		if (command === "status") {
			const statuses = await withPool((client) => migrationStatus(client));
			if (statuses.length === 0) {
				process.stdout.write("No migrations found.\n");
				return 0;
			}
			for (const status of statuses) {
				process.stdout.write(
					`${status.applied ? "applied" : "pending"} ${status.filename}\n`,
				);
			}
			return 0;
		}
		printUsage();
		return 1;
	} catch (error) {
		process.stderr.write(`Migration failed: ${errorMessage(error)}\n`);
		return 1;
	}
}

if (import.meta.main) {
	process.exitCode = await main();
}

/**
 * Typed readers for the docpull context pack contract (v3).
 *
 * A pack directory contains `corpus.manifest.json` (the stable source map)
 * and `documents.ndjson` or `documents.jsonl` (one document record per
 * line). Field names mirror the Python `DocumentRecord` model and the
 * manifest writer; both drop `null` fields on write, so most fields are
 * optional here. Unknown fields are forward-compatible metadata.
 */

import { createReadStream } from "node:fs";
import { access, readFile } from "node:fs/promises";
import { join } from "node:path";

/** One entry of `corpus.manifest.json` `records[]`. */
export interface ManifestRecord {
  schema_version?: number;
  document_id: string;
  url: string;
  title?: string | null;
  content_hash: string;
  fetched_at?: string;
  rendered_at?: string | null;
  content_type?: string;
  mime_type?: string;
  token_count?: number | null;
  route?: Record<string, unknown>;
  rights?: Record<string, unknown>;
  source_type?: string;
  source_citation_id?: string;
  record_citation_id?: string;
  chunk_index?: number;
  chunk_id?: string;
  chunk_heading?: string;
  output_path?: string;
  [key: string]: unknown;
}

/** Shape of `corpus.manifest.json`. */
export interface CorpusManifest {
  schema_version: number;
  output_contract_version?: number;
  generated_at?: string;
  output_format?: string;
  run?: Record<string, unknown> | null;
  document_count: number;
  record_count: number;
  chunk_count?: number;
  archive?: Record<string, unknown>;
  records: ManifestRecord[];
  [key: string]: unknown;
}

/** One line of `documents.ndjson` / `documents.jsonl`. */
export interface DocumentRecord {
  schema_version?: number;
  document_id: string;
  url: string;
  title?: string | null;
  content: string;
  metadata?: Record<string, unknown>;
  extraction?: Record<string, unknown>;
  source_type?: string | null;
  fetched_at?: string;
  rendered_at?: string | null;
  content_type?: string;
  mime_type?: string;
  content_hash: string;
  run?: Record<string, unknown> | null;
  route?: Record<string, unknown>;
  rights?: Record<string, unknown>;
  source_citation_id?: string | null;
  record_citation_id?: string | null;
  chunk_index?: number | null;
  chunk_id?: string | null;
  chunk_heading?: string | null;
  token_count?: number | null;
  [key: string]: unknown;
}

/** Manifest plus materialized document records for one pack directory. */
export interface Pack {
  packDir: string;
  manifest: CorpusManifest;
  documents: DocumentRecord[];
}

const DOCUMENT_FILES = ["documents.ndjson", "documents.jsonl"] as const;

/** Read and parse `corpus.manifest.json` from a pack directory. */
export async function readCorpusManifest(packDir: string): Promise<CorpusManifest> {
  const manifestPath = join(packDir, "corpus.manifest.json");
  let raw: string;
  try {
    raw = await readFile(manifestPath, "utf8");
  } catch (error) {
    throw new Error(`Cannot read corpus manifest at ${manifestPath}: ${errorMessage(error)}`);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`Invalid JSON in ${manifestPath}: ${errorMessage(error)}`);
  }
  if (!isRecord(parsed) || !Array.isArray(parsed["records"])) {
    throw new Error(`Invalid corpus manifest at ${manifestPath}: expected an object with a records array`);
  }
  return parsed as CorpusManifest;
}

/** Iterate document records from `documents.ndjson` or `documents.jsonl`. */
export async function* readDocuments(packDir: string): AsyncGenerator<DocumentRecord, void, undefined> {
  const documentsPath = await resolveDocumentsPath(packDir);
  let lineNumber = 0;
  for await (const line of readLines(documentsPath)) {
    lineNumber += 1;
    if (line.trim().length === 0) {
      continue;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch (error) {
      throw new Error(`Invalid NDJSON in ${documentsPath} line ${lineNumber}: ${errorMessage(error)}`);
    }
    if (!isRecord(parsed)) {
      throw new Error(`Invalid NDJSON in ${documentsPath} line ${lineNumber}: expected an object`);
    }
    yield parsed as DocumentRecord;
  }
}

/** Read a whole pack: manifest plus every document record. */
export async function readPack(packDir: string): Promise<Pack> {
  const manifest = await readCorpusManifest(packDir);
  const documents: DocumentRecord[] = [];
  for await (const record of readDocuments(packDir)) {
    documents.push(record);
  }
  return { packDir, manifest, documents };
}

async function resolveDocumentsPath(packDir: string): Promise<string> {
  for (const name of DOCUMENT_FILES) {
    const candidate = join(packDir, name);
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Try the next candidate file name.
    }
  }
  throw new Error(`No documents file found in ${packDir}; expected one of: ${DOCUMENT_FILES.join(", ")}`);
}

async function* readLines(path: string): AsyncGenerator<string, void, undefined> {
  const stream = createReadStream(path, { encoding: "utf8" });
  let buffered = "";
  for await (const chunk of stream) {
    buffered += String(chunk);
    let newlineIndex = buffered.indexOf("\n");
    while (newlineIndex !== -1) {
      yield buffered.slice(0, newlineIndex).replace(/\r$/, "");
      buffered = buffered.slice(newlineIndex + 1);
      newlineIndex = buffered.indexOf("\n");
    }
  }
  if (buffered.length > 0) {
    yield buffered.replace(/\r$/, "");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

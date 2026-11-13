import { describe, expect, test } from "bun:test";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { readCorpusManifest, readDocuments, readPack, type DocumentRecord } from "./pack.js";

const MANIFEST = {
  schema_version: 3,
  output_contract_version: 3,
  generated_at: "2026-07-21T00:00:00+00:00",
  output_format: "ndjson",
  document_count: 2,
  record_count: 2,
  chunk_count: 1,
  records: [
    {
      schema_version: 3,
      document_id: "doc_first",
      url: "https://example.com/first",
      title: "First",
      content_hash: "hash_first",
      token_count: 4,
      output_path: "sources/01.md",
    },
    {
      schema_version: 3,
      document_id: "doc_second",
      url: "https://example.com/second",
      title: "Second",
      content_hash: "hash_second",
      chunk_id: "chunk_second",
      chunk_index: 0,
      chunk_heading: "Intro",
      token_count: 5,
      output_path: "sources/02.md",
    },
  ],
};

const DOCUMENTS: DocumentRecord[] = [
  {
    schema_version: 3,
    document_id: "doc_first",
    url: "https://example.com/first",
    title: "First",
    content: "First document body.",
    content_hash: "hash_first",
    token_count: 4,
  },
  {
    schema_version: 3,
    document_id: "doc_second",
    url: "https://example.com/second",
    title: "Second",
    content: "Second document body.",
    content_hash: "hash_second",
    chunk_id: "chunk_second",
    chunk_index: 0,
    chunk_heading: "Intro",
    token_count: 5,
  },
];

async function writePack(documentsFile: string): Promise<string> {
  const packDir = await mkdtemp(join(tmpdir(), "docpull-sdk-test-"));
  await writeFile(join(packDir, "corpus.manifest.json"), JSON.stringify(MANIFEST), "utf8");
  const ndjson = `${DOCUMENTS.map((record) => JSON.stringify(record)).join("\n")}\n`;
  await writeFile(join(packDir, documentsFile), ndjson, "utf8");
  return packDir;
}

describe("readCorpusManifest", () => {
  test("parses manifest fields and records", async () => {
    const packDir = await writePack("documents.ndjson");
    const manifest = await readCorpusManifest(packDir);

    expect(manifest.schema_version).toBe(3);
    expect(manifest.document_count).toBe(2);
    expect(manifest.record_count).toBe(2);
    expect(manifest.chunk_count).toBe(1);
    expect(manifest.records).toHaveLength(2);
    expect(manifest.records[0]?.document_id).toBe("doc_first");
    expect(manifest.records[0]?.output_path).toBe("sources/01.md");
    expect(manifest.records[1]?.chunk_id).toBe("chunk_second");
    expect(manifest.records[1]?.token_count).toBe(5);
  });

  test("rejects when the manifest is missing", async () => {
    const packDir = await mkdtemp(join(tmpdir(), "docpull-sdk-test-"));
    await expect(readCorpusManifest(packDir)).rejects.toThrow("Cannot read corpus manifest");
  });
});

describe("readDocuments", () => {
  test("iterates ndjson records in file order", async () => {
    const packDir = await writePack("documents.ndjson");
    const records: DocumentRecord[] = [];
    for await (const record of readDocuments(packDir)) {
      records.push(record);
    }

    expect(records.map((record) => record.document_id)).toEqual(["doc_first", "doc_second"]);
    expect(records[0]?.content).toBe("First document body.");
    expect(records[1]?.chunk_id).toBe("chunk_second");
    expect(records[1]?.chunk_index).toBe(0);
  });

  test("falls back to documents.jsonl", async () => {
    const packDir = await writePack("documents.jsonl");
    const records: DocumentRecord[] = [];
    for await (const record of readDocuments(packDir)) {
      records.push(record);
    }

    expect(records).toHaveLength(2);
    expect(records[0]?.url).toBe("https://example.com/first");
  });

  test("rejects when no documents file exists", async () => {
    const packDir = await mkdtemp(join(tmpdir(), "docpull-sdk-test-"));
    const iterate = async () => {
      for await (const _record of readDocuments(packDir)) {
        // Consume the iterator so the error surfaces.
      }
    };
    await expect(iterate()).rejects.toThrow("documents.ndjson, documents.jsonl");
  });

  test("rejects invalid ndjson lines with the line number", async () => {
    const packDir = await mkdtemp(join(tmpdir(), "docpull-sdk-test-"));
    await writeFile(join(packDir, "documents.ndjson"), `${JSON.stringify(DOCUMENTS[0])}\nnot-json\n`, "utf8");
    const iterate = async () => {
      for await (const _record of readDocuments(packDir)) {
        // Consume the iterator so the error surfaces.
      }
    };
    await expect(iterate()).rejects.toThrow("line 2");
  });
});

describe("runtime validation", () => {
  async function iterateAll(packDir: string): Promise<void> {
    for await (const _record of readDocuments(packDir)) {
      // Consume the iterator so the error surfaces.
    }
  }

  test("rejects an unknown manifest schema major", async () => {
    const packDir = await writePack("documents.ndjson");
    await writeFile(join(packDir, "corpus.manifest.json"), JSON.stringify({ ...MANIFEST, schema_version: 4 }), "utf8");
    await expect(readCorpusManifest(packDir)).rejects.toThrow("schema_version 4");
  });

  test("rejects a manifest without schema_version", async () => {
    const packDir = await writePack("documents.ndjson");
    const { schema_version: _omit, ...rest } = MANIFEST;
    await writeFile(join(packDir, "corpus.manifest.json"), JSON.stringify(rest), "utf8");
    await expect(readCorpusManifest(packDir)).rejects.toThrow("schema_version must be a number");
  });

  test("rejects manifest records missing required fields", async () => {
    const packDir = await writePack("documents.ndjson");
    const broken = { ...MANIFEST, records: [{ document_id: "doc_x", url: "https://example.com/x" }] };
    await writeFile(join(packDir, "corpus.manifest.json"), JSON.stringify(broken), "utf8");
    await expect(readCorpusManifest(packDir)).rejects.toThrow('records[0]: required field "content_hash"');
  });

  test("rejects document records missing content", async () => {
    const packDir = await mkdtemp(join(tmpdir(), "docpull-sdk-test-"));
    const { content: _omit, ...rest } = { ...DOCUMENTS[0] };
    await writeFile(join(packDir, "documents.ndjson"), `${JSON.stringify(rest)}\n`, "utf8");
    await expect(iterateAll(packDir)).rejects.toThrow('line 1: required field "content"');
  });

  test("rejects document records with an unknown schema major", async () => {
    const packDir = await mkdtemp(join(tmpdir(), "docpull-sdk-test-"));
    await writeFile(join(packDir, "documents.jsonl"), `${JSON.stringify({ ...DOCUMENTS[0], schema_version: 2 })}\n`, "utf8");
    await expect(iterateAll(packDir)).rejects.toThrow("schema_version 2");
  });
});

describe("Python-produced pack fixture", () => {
  // Written by docpull's own NDJSON/manifest writers (synthetic content only).
  const fixtureDir = fileURLToPath(new URL("../test-fixtures/python-pack", import.meta.url));

  test("reads the manifest and documents written by the Python packer", async () => {
    const pack = await readPack(fixtureDir);
    expect(pack.manifest.schema_version).toBe(3);
    expect(pack.manifest.output_contract_version).toBe(3);
    expect(pack.manifest.records).toHaveLength(pack.manifest.record_count);
    expect(pack.documents).toHaveLength(pack.manifest.record_count);
    expect(pack.documents.map((doc) => doc.document_id)).toEqual(
      pack.manifest.records.map((record) => record.document_id),
    );
    expect(pack.documents[0]?.url).toBe("https://docs.example.test/auth");
    expect(pack.documents[0]?.content).toContain("bearer tokens");
    expect(pack.documents[0]?.content_hash).toBe(pack.manifest.records[0]?.content_hash);
  });
});

describe("readPack", () => {
  test("returns manifest plus materialized documents", async () => {
    const packDir = await writePack("documents.ndjson");
    const pack = await readPack(packDir);

    expect(pack.packDir).toBe(packDir);
    expect(pack.manifest.record_count).toBe(2);
    expect(pack.documents).toHaveLength(2);
    expect(pack.documents[1]?.document_id).toBe("doc_second");
  });
});

# JS/TS SDK and Framework Loaders

docpull writes context packs to disk: `corpus.manifest.json`,
`documents.ndjson` (or `documents.jsonl`), Markdown source files, and
sidecars. This page covers the consumers that read those packs directly:
the `@docpull/sdk` TypeScript package and the Python loaders for LangChain
and LlamaIndex. All of them read local files only; none of them touch the
network.

## Python: LangChain loader

`DocpullPackLoader` implements the LangChain `BaseLoader` interface
(`lazy_load` and `load`). It requires `langchain-core`, which docpull does
not install for you:

```bash
pip install langchain-core
```

```python
from docpull.integrations.langchain import DocpullPackLoader

loader = DocpullPackLoader("./packs/example")
for document in loader.lazy_load():
    print(document.metadata["url"], document.metadata["token_count"])

documents = loader.load()  # eager list
```

Each pack record becomes one `langchain_core.documents.Document` with
`page_content` set to the record content and metadata keys `url`, `title`,
`document_id`, `chunk_id`, `content_hash`, `token_count`, and `source`
(the source URL). Chunked packs yield one Document per chunk record.

## Python: LlamaIndex reader

`DocpullPackReader` follows the LlamaIndex reader convention (`load_data`
and `lazy_load_data`) and requires `llama-index-core`:

```bash
pip install llama-index-core
```

```python
from docpull.integrations.llamaindex import DocpullPackReader

reader = DocpullPackReader("./packs/example")
documents = reader.load_data()
print(documents[0].text, documents[0].metadata["document_id"])
```

Both loaders read `documents.ndjson`/`documents.jsonl` when present and
fall back to `corpus.manifest.json` records plus their `output_path`
Markdown files. Record order follows the pack files, so repeated loads are
deterministic. Both classes are also importable from
`docpull.integrations` directly; the framework import happens lazily, so
importing the module never requires the framework.

## JavaScript/TypeScript: @docpull/sdk

The SDK lives in `sdk/js` and ships typed readers for the v3 pack contract
plus a thin wrapper around the `docpull` CLI.

```bash
bun add @docpull/sdk   # or: npm install @docpull/sdk
```

### Read a pack

```ts
import { readCorpusManifest, readDocuments, readPack } from "@docpull/sdk";

const manifest = await readCorpusManifest("./packs/example");
console.log(manifest.record_count, manifest.records[0]?.output_path);

for await (const record of readDocuments("./packs/example")) {
  console.log(record.document_id, record.chunk_id ?? "whole-document");
}

const pack = await readPack("./packs/example");
console.log(pack.documents.length);
```

`CorpusManifest`, `ManifestRecord`, and `DocumentRecord` mirror the field
names the Python side writes. Fields that the writers omit when null are
optional, and unknown fields stay accessible as forward-compatible
metadata, matching the manifest stability contract.

### Fetch with the CLI

`fetchToPack` and `runDocpull` spawn the `docpull` CLI (it must be on
PATH, or pass `bin`). Arguments are passed as an array with `shell: false`,
so nothing is shell-interpolated. Non-zero exits reject with a
`DocpullCliError` carrying `exitCode` and `stderr`.

```ts
import { fetchToPack, runDocpull } from "@docpull/sdk";

// docpull https://example.com/docs -o ./packs/example --budget 0
await fetchToPack("https://example.com/docs", "./packs/example", { budget: 0 });

const result = await runDocpull(["pack", "validate", "./packs/example"]);
console.log(result.stdout);
```

## Related pages

- [Context Pack Contract v3](context-pack-contract-v3.md)
- [Corpus Manifest](corpus-manifest.md)

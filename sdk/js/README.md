# @docpull/sdk

TypeScript SDK for [docpull](https://github.com/raintree-technology/docpull).
It reads local context packs (the v3 pack contract) and runs the `docpull`
CLI from Node or Bun. Reading a pack never touches the network; fetching
requires the `docpull` CLI on PATH.

## Install

```bash
bun add @docpull/sdk   # or: npm install @docpull/sdk
```

## Read a pack

```ts
import { readCorpusManifest, readDocuments, readPack } from "@docpull/sdk";

const manifest = await readCorpusManifest("./packs/example");
console.log(manifest.record_count, manifest.records[0]?.url);

// Stream records one line at a time.
for await (const record of readDocuments("./packs/example")) {
  console.log(record.document_id, record.token_count);
}

// Or load everything at once.
const pack = await readPack("./packs/example");
console.log(pack.documents.length);
```

`readDocuments` prefers `documents.ndjson` and falls back to
`documents.jsonl`. Types (`CorpusManifest`, `ManifestRecord`,
`DocumentRecord`) mirror the field names written by the Python side and keep
unknown fields as forward-compatible metadata.

## Run the CLI

```ts
import { fetchToPack, runDocpull } from "@docpull/sdk";

// docpull https://example.com/docs -o ./packs/example --budget 0
await fetchToPack("https://example.com/docs", "./packs/example", { budget: 0 });

// Any other subcommand.
const result = await runDocpull(["pack", "validate", "./packs/example"]);
console.log(result.stdout);
```

Commands are spawned with an argument array and `shell: false`. A non-zero
exit rejects with a `DocpullCliError` that carries `exitCode` and `stderr`.

## Development

```bash
bun install        # from the repo root
bun test           # from sdk/js
bun run typecheck  # tsc --noEmit
```

## License

MIT

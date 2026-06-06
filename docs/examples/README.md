# CLI Recipes

These examples use the current `docpull` CLI. The old multi-source YAML runner
and options such as `--sources-file`, `language`, `keep_variant`, TOON output,
and `create_index` are not part of the current CLI surface.

## Single Page

```bash
docpull https://docs.example.com/guide --single -o ./docs/example
```

## Small Crawl

```bash
docpull https://docs.example.com --max-pages 50 --max-depth 2 -o ./docs/example
```

## LLM Chunks

```bash
docpull https://docs.example.com \
  --profile llm \
  --stream \
  | jq .
```

## Selective Crawl

```bash
docpull https://docs.example.com \
  --include-paths "/api/*" "/reference/*" \
  --exclude-paths "/changelog/*" "/release-notes/*" \
  -o ./docs/example-api
```

## Incremental Mirror

```bash
docpull https://docs.example.com \
  --profile mirror \
  --cache \
  --cache-dir .docpull-cache \
  -o ./docs/example-mirror
```

## Output Formats

```bash
docpull https://docs.example.com --format markdown -o ./out/markdown
docpull https://docs.example.com --format json -o ./out/json
docpull https://docs.example.com --format ndjson -o ./out/ndjson
docpull https://docs.example.com --format sqlite -o ./out/sqlite
```

## Claude Code Skill

```bash
docpull https://docs.example.com \
  --skill example-docs \
  --skill-description "Example documentation reference"
```

Run `docpull --help` for the full option list.

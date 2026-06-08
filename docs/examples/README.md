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

## Parallel Context Pack

```bash
docpull parallel demo --output-dir ./packs/demo
```

From a source checkout, you can import the checked-in fixture directly:

```bash
docpull parallel import ./docs/examples/parallel-search-extract.json --output-dir ./packs/demo
```

Live Parallel workflows use your own API key:

```bash
pip install 'docpull[parallel]'
docpull parallel init
docpull parallel auth --json

# CI can still use a normal environment secret.
export PARALLEL_API_KEY="<your-parallel-api-key>"

docpull parallel context-pack "Compare AI web-search APIs for agents" \
  --query "AI web search API" \
  --query "agent web extraction API" \
  --include-domain parallel.ai \
  --exclude-domain onparallel.com \
  --output-dir ./packs/ai-web-search \
  --max-estimated-cost 0.05
```

Preview a live workflow without spending credits:

```bash
docpull parallel context-pack "Compare AI web-search APIs for agents" \
  --query "AI web search API" \
  --query "agent web extraction API" \
  --include-domain parallel.ai \
  --fetch-max-age-seconds 3600 \
  --excerpt-chars-per-result 5000 \
  --dry-run
```

Build other Parallel-backed artifacts:

```bash
docpull parallel search-pack "Parallel Search API docs" \
  --query "Parallel Search API" \
  --include-domain docs.parallel.ai \
  --dry-run

docpull parallel discover-docs "Find canonical Parallel API docs" \
  --query "Parallel Search API docs" \
  --include-domain docs.parallel.ai \
  --crawl-profile mirror \
  --dry-run

docpull parallel extract-pack https://docs.parallel.ai/api-reference/search/search \
  --objective "Extract the Parallel Search API reference" \
  --dry-run

docpull parallel fallback-pack https://docs.parallel.ai/api-reference/search/search \
  --profile rag \
  --dry-run

docpull parallel task-pack "Research Parallel Search API changes" \
  --source-include-domain docs.parallel.ai \
  --output-schema-json '{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"]}' \
  --dry-run

docpull parallel entity-pack "AI developer infrastructure companies" \
  --entity-type companies \
  --match-limit 25 \
  --dry-run

docpull parallel findall-pack "AI developer infrastructure companies" \
  --condition "devtool=Company must sell developer infrastructure" \
  --generator preview \
  --match-limit 5 \
  --dry-run

docpull parallel taskgroup-pack ./companies.json \
  --prompt-template "Research {company}" \
  --processor lite \
  --wait \
  --dry-run

docpull parallel monitor-pack create "New vendor pricing changes" --dry-run
docpull parallel monitor-pack create --type snapshot --task-run-id run_123 --dry-run
docpull parallel monitor-pack list --status active --output-dir ./packs/monitors
docpull parallel monitor-pack events monitor_123 --cursor next_cursor --output-dir ./packs/monitor-events
docpull parallel api-pack https://docs.parallel.ai/llms.txt --output-dir ./packs/parallel-docs-index
```

Ready-made API-pack recipes are checked in:

```bash
docpull parallel run ./docs/examples/parallel-llms-api-pack.yaml --dry-run
docpull parallel run ./docs/examples/parallel-openapi-api-pack.yaml --dry-run
```

Inspect packs locally:

```bash
docpull pack score ./packs/demo
docpull pack sources ./packs/demo --require-domain docs.parallel.ai
docpull pack diff ./packs/old ./packs/new --markdown ./packs/changes.md
docpull parallel diff-brief ./packs/old ./packs/new --dry-run
```

Run `docpull --help` for the full option list.

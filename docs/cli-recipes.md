# DocPull CLI recipes

These examples cover the supported release surface: context dependencies,
v3 pack contracts, validation/preparation, exports, Context CI, document
parsing, OpenAPI packs, explicit rendering, and monitors.

## Project Context

```bash
docpull init stripe-docs
docpull add stripe react postgres
docpull install
docpull sync
docpull deps
docpull diff
docpull review
docpull export context-pack --target codex
```

## Pack Contract

```bash
docpull https://docs.example.com -o packs/docs
docpull pack validate packs/docs --level raw
docpull pack prepare packs/docs --eval-grade
docpull pack validate packs/docs --level eval
docpull export packs/docs --format openai-vector-jsonl -o exports/openai.jsonl
```

## Local Inputs

```bash
docpull parse ./handbook.pdf -o packs/handbook --backend auto
docpull openapi-pack ./openapi.json -o packs/api
docpull feed-pack ./feed.xml -o packs/feed
docpull paper-pack ./paper.md arxiv:1706.03762 -o packs/papers
docpull repo-pack psf/requests -o packs/repo
docpull package-pack pypi:requests -o packs/package
docpull standards-pack rfc:9110 -o packs/standard
docpull dataset-pack ./metrics.csv -o packs/dataset
docpull transcript-pack ./meeting.vtt -o packs/transcript
```

## Context CI

```bash
docpull ci --prepare
```

Minimal GitHub Actions step:

```yaml
- run: pip install docpull
- run: docpull ci --prepare
```

## Web Search

`docpull search` queries a search backend you choose explicitly. DocPull has no
index of its own and never calls a paid search provider for this command.

```bash
# Self-hosted SearXNG with `json` enabled under search.formats in settings.yml
export DOCPULL_SEARXNG_URL=http://localhost:8888
docpull search "rate limit headers" --backend searxng --include-domain docs.github.com

# Marginalia's independent index with the shared public key
docpull search "static site generators" --backend marginalia --max-results 5 --json
```

| Backend | Setup | Notes |
| --- | --- | --- |
| `searxng` | Run a SearXNG instance and pass `--searxng-url` or set `DOCPULL_SEARXNG_URL` | Metasearch across the engines your instance enables; the configured URL is trusted, so it may be local |
| `marginalia` | None; set `DOCPULL_MARGINALIA_KEY` for your own key | The shared `public` key is often rate limited; results carry Marginalia's license, CC-BY-NC-SA 4.0 for free keys |

Each query returns at most 20 results. `--include-domain` and `--exclude-domain`
filter results by host. One include domain also narrows the upstream query.
`--include-content` fetches every result through the same local pipeline as
`docpull sync`, with HTTPS-only fetching, private-network blocking, and robots.txt
handling, and attaches the Markdown.

## Research

`docpull research` answers a question from the web with cited passages. It searches
with the chosen backend and fetches up to `--max-sources` results (default 5,
at most 10) through the local pipeline. It ranks paragraphs with the same scoring
as `docpull query` and returns the best `--max-passages` (default 8), at most three
per source. Each passage keeps its exact character span and source ID.

```bash
docpull research "What is the GitHub REST API rate limit?" --backend searxng \
  --include-domain docs.github.com

# Optional: rewrite the cited passages with a local OpenAI-compatible model
docpull research "What is the GitHub REST API rate limit?" --backend searxng \
  --llm-url http://localhost:11434/v1 --llm-model llama3.1
```

Without a model, the answer is the ranked passages, each ending in a citation such
as `[S1]`. With `--llm-url` and `--llm-model`, the model may answer only from those
passages. Its answer is kept only when every citation names a real source. If it
cites nothing or names an unknown source, the cited passages are returned with a
warning. A model endpoint outside localhost needs `--allow-remote-llm`, because it
may cost money. `--llm-api-key-env NAME` sends a bearer key from the environment.

## Local Extract API

`docpull serve --compat` accepts the request shapes of common hosted extract
endpoints and answers them from the local fetch pipeline. Agent code that already
calls one of those APIs can switch by changing its base URL:

```bash
pip install "docpull[serve]"
docpull serve --compat --port 8765
```

| Route | Request shape | Response shape |
| --- | --- | --- |
| `POST /extract` | Tavily extract: `urls` (string or list) | `results[].raw_content`, `failed_results[]` |
| `POST /contents` | Exa contents: `urls` or `ids`, `text` (bool or `maxCharacters`) | `results[].text`, `statuses[]` |
| `POST /v1/scrape`, `POST /v2/scrape` | Firecrawl scrape: `url`, `formats: ["markdown"]` | `success`, `data.markdown`, `data.metadata` |

Start the server with `--search-backend searxng` or `--search-backend marginalia`
to also serve search shapes. Those are Tavily or Exa `POST /search`, chosen by the
request fields, and Firecrawl `POST /v1/search` and `/v2/search`. The same server
answers Exa `POST /answer` and fills Tavily's `answer` when a search sets
`include_answer`. Both use `docpull research` ranking, plus the model from
`--llm-url` and `--llm-model` when one is configured. A client can
pin a flavor by setting its base URL to `http://127.0.0.1:8765/tavily`,
`/exa`, or `/firecrawl`. Without a search backend, the search routes return
HTTP 501.

The server also runs async jobs in the shapes those APIs use:

| Create | Poll | Notes |
| --- | --- | --- |
| Firecrawl `POST /v1/crawl` or `/v2/crawl` with `url`, `limit` (default 50, at most 500), `maxDiscoveryDepth`, `includePaths`, `excludePaths` | `GET /v{1,2}/crawl/{id}`; `DELETE` cancels | Discovery and fetching use the local crawler. Path filters are regular expressions on the URL path, as in Firecrawl. `creditsUsed` is always 0 |
| Parallel `POST /v1/tasks/runs` with `input` | `GET /v1/tasks/runs/{run_id}`; `GET .../result?timeout=` waits up to 600 seconds | Runs `docpull research`. `output.basis[0].citations` lists each source with its passages as `excerpts`. Needs `--search-backend` |
| Tavily `POST /research` with `input` | `GET /research/{request_id}` returns 202 until done | Runs `docpull research`. Returns `content` and `sources`. Needs `--search-backend` |

Jobs live in memory, so they are lost when the server stops. Finished jobs expire
after 24 hours, and the server keeps at most 200 jobs.

Every page goes through the same fetcher as `docpull sync`. That means
HTTPS-only fetching, private-network blocking, and robots.txt handling. Nothing is
sent to the hosted services. A request takes at most 20 URLs. The server binds to
localhost. Exposing it with `--host 0.0.0.0 --allow-network-bind` also requires
`--api-key-env NAME`, and clients must then send that key as a bearer token or
`x-api-key`. Only Markdown output is served. Other Firecrawl formats return HTTP
400.

## Rendering

Rendering is off by default. Explicit rendering uses the canonical
`agent-browser --json` contract locally and inside supported cloud sandboxes.

```bash
docpull render https://example.com/app --runtime local --check
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 \
  docpull render https://example.com/app --runtime local -o rendered
```

Cloud rendering remains explicit:

```bash
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 \
  docpull render https://example.com/app --runtime e2b --budget 1
```

## Release Smoke

Run the real-data free/local surface smoke before cutting a release candidate:

```bash
python scripts/release_a_plus_check.py --strict
python scripts/real_feature_smoke.py --json --full-mcp --strict-ci --auth-matrix --monitor-soak-minutes 10
```

`release_a_plus_check.py --strict` requires synchronized generated metadata and
a clean `git status --short`; run it from the exact tree you intend to tag.

Cloud/keyed lanes stay explicit:

```bash
python scripts/real_feature_smoke.py --include-cloud --json
```

## Monitors

```bash
docpull monitor init
docpull monitor list
docpull monitor run
docpull monitor report
```

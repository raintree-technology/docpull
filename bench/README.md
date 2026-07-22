# DocPull Evaluation Lab

`bench/` is an isolated uv application for internal product decisions. It does
not change DocPull's public CLI, SDK, MCP server, artifact contract, 10k
benchmark, or legacy benchmark module.

The lab is local-first, deterministic, content-free in reports, and fail-closed
on paid work. Pydantic Evals schedules trials; the framework-neutral schema and
canonical lane scorers remain authoritative.

## Reproduce it

One command replays the committed controlled corpus (212 cases, no network,
no spend) and rescores it:

```bash
uv sync --project bench --locked --dev
uv run --project bench --locked docpull-bench run bench/cases/controlled-v2.yaml \
  --adapter replay --system fixture --version 2 \
  --replay-dir bench/replays/controlled-v2 --output-dir bench/runs/controlled \
  --network-isolation enforced
```

Raw traces land under `bench/runs/controlled/<run-id>/`: `report.json`,
`observations.ndjson`, `scores.ndjson`, and an `artifacts/` directory for
adapters that write files. Reports stay content-free (hashes, lengths,
statuses, metric vectors). This lab informs internal product decisions; it is
not a public leaderboard (see [POSITIONING.md](POSITIONING.md)).

## Rules

- Gold expectations stay in the harness and never cross an adapter boundary.
- A case passes only when all required assertions pass.
- Metrics remain separated by lane; there is no global score or winner.
- Unsupported capability returns `unsupported`, not a fabricated failure.
- Live gold expires and must be manually rechecked.
- Hosted runs require both credentials and `--max-cost-usd`; paid retries are
  disabled and the conservative full run is reserved before credentials are
  read.
- Portable reports contain sanitized URLs, hashes, lengths, timings, usage,
  costs, statuses, and metric vectors—not fetched bodies.
- New runs write integrity-checked portable report schema v3 and scorer v5;
  schema-v2 reports remain readable as legacy history but are never claim-ready.
- Extract and crawl scores carry diagnostic token-economics metrics
  (`total_tokens`, `tokens_per_page`, `token_estimator`, and
  `html_input_tokens`/`token_reduction_vs_html` when the case maps to
  committed fixture HTML). They never gate pass/fail. The estimator is
  tiktoken cl100k_base when importable, else a labeled
  `max(words, chars/4)` heuristic recorded per score.

## Lanes and corpora

| Lane | Controlled/live corpus |
| --- | ---: |
| Extract | 12 owned pages; 32-case historical live suite retained provisionally |
| Crawl | 6 owned graphs; 8-site historical live suite retained provisionally |
| Parse | 10 text, Markdown, DOCX, PDF, malformed, encrypted, and OCR-gated files |
| Pack | 10 raw/agent/eval scenarios |
| Structured | 12 fixed documents and JSON Schemas |
| Lifecycle | 10 unified public-CLI checks |
| Change | 12 controlled state transitions |
| Retrieval | 100 frozen-pack queries, including 20 unanswerable |
| Search | 30 manual live queries across five families |
| Research | 20 fixed-corpus exact evidence tasks |
| Policy | 20 controlled adversarial cases |

## Local use

```bash
uv sync --project bench --locked --dev
uv run --project bench --locked docpull-bench fixtures verify
uv run --project bench --locked docpull-bench validate bench/cases/controlled-v2.yaml
uv run --project bench --locked docpull-bench run bench/cases/controlled-v2.yaml \
  --adapter replay --system fixture --version 2 \
  --replay-dir bench/replays/controlled-v2 --output-dir bench/runs/controlled \
  --network-isolation enforced
uv run --project bench --locked docpull-bench lifecycle
uv run --project bench --locked docpull-bench context --repeat 2
```

The `lifecycle` command is an alias for `cases/lifecycle-v2.yaml` in the unified
runner. The `context` profile runs 130 controlled parse, pack, lifecycle, and
retrieval cases through the real DocPull public CLI with no network or provider
spend. Change and policy fixtures remain replay-only until their inputs encode
the public CLI state transitions they claim to test. The profile measures the
context-dependency contract rather than reducing unlike product capabilities
to one global score. Regenerate the owned corpus with
`uv run --project bench python bench/scripts/generate_fixtures.py`; committed
DOCX and PDF bytes must remain identical.

## Providers

Native comparable adapters exist for Tavily, Exa, and Parallel extract/search,
Firecrawl extract/search/crawl, Tavily and Context.dev crawl, and Context.dev
Markdown extract. Prices come from
`pricing/providers.yaml`, including dated official source URLs. No provider
workflow is scheduled.

```bash
TAVILY_API_KEY=... uv run --project bench --locked docpull-bench run \
  bench/cases/live-search-v2.yaml --adapter tavily-search \
  --system tavily-search --max-cost-usd 0.48 --environment-label manual-owned
```

The command adapter receives only a minimal base environment plus explicit
`--allow-env` names. Its output cannot override case, system, version, or timing
identity.

## Local OSS baselines

Zero-cost local adapters run popular open-source extractors on the same
committed fixture bytes the controlled corpus serves to live adapters:

| Adapter | Lanes | Notes |
| --- | --- | --- |
| `trafilatura` | extract | Markdown output when the installed release supports it, else plain text. |
| `readability` | extract | readability-lxml main content plus a minimal stdlib HTML→Markdown conversion. |
| `crawl4ai` | extract | Runs Crawl4AI's HTML→Markdown generator on fixture bytes. The crawl lane is not claimed: Crawl4AI crawling requires a live Playwright browser and network, which the controlled replay policy forbids. |

Dependencies are optional. Install them with
`uv sync --project bench --locked --extra baselines`. A missing dependency
yields a failed observation naming the missing package, mirroring hosted
adapters with missing API keys; it never crashes a run.

```bash
uv run --project bench --locked docpull-bench run bench/cases/controlled-v2.yaml \
  --adapter trafilatura --system trafilatura --output-dir bench/runs/baselines
```

## Baselines and publication

```bash
docpull-bench baseline check REPORT bench/baselines/controlled-v2.fixture.json
docpull-bench baseline update REPORT BASELINE --reason 'reviewed protocol change'
docpull-bench compare REPORT_A REPORT_B --markdown COMPARISON.md
docpull-bench publish create SUITE REPORT_A REPORT_B --output-dir BUNDLE
docpull-bench publish sign BUNDLE
docpull-bench publish verify BUNDLE --trusted-gpg-fingerprint FINGERPRINT
```

Critical controlled pass→fail changes block. Performance changes above both 20%
and the 100 ms/10 MiB floors are advisory. Verification recomputes publication
hashes, reparses reports, and regenerates the comparison; narrative findings
remain hand-reviewed.

For sensitive live diagnostics, `--evidence-dir` and `--evidence-recipient`
encrypt canonical normalized output directly to an external age escrow. The
report retains plaintext commitments and ciphertext hashes only. DocPull claim
subjects must use `--docpull-python` plus `--subject-artifact` to bind an
isolated clean wheel rather than the harness interpreter.

Current manual live evidence and its hand-reviewed decision note are indexed in
[`results/manual/README.md`](results/manual/README.md).
The authoritative non-mutating status overlay is
[`results/STATUS.yaml`](results/STATUS.yaml). The repository-hosted manual
workflow is exploratory only.

## Public-claim readiness

Data-only publication is not authorization to make a comparative claim. The
separate fail-closed gate requires a 100-case, five-trial, independently
reviewed, blinded corpus plus signed provider protocols and reconciled billing:

```bash
docpull-bench claim check PRIVATE_SUITE REPORT... \
  --policy bench/claim/policy-v2.yaml --evidence SIGNED_EVIDENCE.yaml
```

Validate a proposed suite before external custody with the stricter structural
gate. Passing this check is necessary but not sufficient for a claim:

```bash
docpull-bench validate PRIVATE_SUITE --claim-grade
```

The frozen quality-max configuration for a future external extraction study is
[`protocols/future-extraction-quality-v1.yaml`](protocols/future-extraction-quality-v1.yaml).
It is a predeclaration, not a completed run.

Create a future held set from a draft that has never entered the repository.
The public challenge contains development cases plus only IDs and a commitment
for the held cases; both held inputs and expectations remain private:

```bash
docpull-bench challenge export /outside/repo/private-draft.yaml \
  --challenge bench/cases/public-challenge.yaml \
  --gold /outside/repo/private-gold.yaml \
  --manifest bench/cases/public-challenge.manifest.json
```

After an authorized custodian decrypts the private holdout, materialize the
full suite outside the repository for the run:

```bash
docpull-bench challenge materialize bench/cases/public-challenge.yaml \
  /outside/repo/private-gold.yaml \
  --output /outside/repo/materialized-private-suite.yaml
```

Plaintext drafts, gold, and materialized private suites are refused inside the
repository and written mode `0600`. See [`claim/README.md`](claim/README.md).
Encrypt the private gold explicitly with an external age recipient before
custody transfer; this command never deletes the plaintext or treats its
unsigned manifest as claim evidence:

```bash
docpull-bench challenge seal /outside/repo/private-gold.yaml \
  --ciphertext /outside/repo/private-gold.age --recipient AGE_RECIPIENT \
  --manifest /outside/repo/seal.manifest.json
```
Comparisons report operational success, quality conditional on completed
output, and the strict end-to-end pass rate separately. Effect sizes include a
deterministic paired-bootstrap interval; Holm correction is scoped to each
declared hypothesis slice.

## WANDR

`experimental/external-suites/wandr/lock.json` pins the optional WANDR checkout. Run
`experimental/external-suites/wandr/check.sh check` only as a zero-call compatibility probe. WANDR's
judge-based verifier conflicts with this lab's deterministic-only policy, so it
is not a scored lane or claim source.

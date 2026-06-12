# Eval/Benchmark System Audit — 2026-06-10

Scope: the docpull **eval/benchmark Python core** —
`src/docpull/benchmark.py`, `parallel_workflows.py`, `pack_tools.py`,
`source_scoring.py`, `metadata_extractor.py`. CI workflow, secret-store
helpers (`provider_keys.py`/`provider_cli.py`), and the web publication path
were explicitly out of scope for this pass.

Method: multi-agent finder + adversarial-verifier sweep across 9 audit
dimensions (Sonnet), each candidate finding independently re-checked against
current source by a skeptic prompted to refute. 41 agents, 32 findings raised,
**27 confirmed / 5 refuted**. Opus triaged, deduped to 8 root causes, and
remediated.

Threat model: a malicious/compromised provider API response (Tavily, Exa,
Parallel), a malicious recipe/fixture file, or untrusted fetched doc content
written into agent-consumed artifacts. docpull is a local-first CLI, so
"remote exploit" generally means "a shared/CI recipe or a misbehaving paid
provider," not a network-facing service.

## Fixed

### Security
1. **Recipe `output_dir` path traversal** (`parallel_workflows.py`) — a recipe
   field could write pack files to any absolute path or `..`-escape the cwd
   (two independent code paths: `_recipe_output_dir` and the inline
   context-pack resolver). Added `_ensure_within_cwd` containment; both paths
   now route through `_recipe_output_dir`. The CLI `--output-dir` override
   stays trusted/exempt.
2. **Prompt-injection via provider Markdown** — provider-supplied `title`/`url`
   were written verbatim into `AGENT_CONTEXT.md`, `sources.md`, and
   `NEXT_STEPS.md` (LLM-consumed). Added shared `_md_link` / `_md_inline_text`
   / `_md_safe_url` helpers (escape `[]` `` ` ``, strip CR/LF, http(s)-only
   URLs) and applied them at every writer site in both files.
3. **`_http_json_post_once` hardening** (`benchmark.py`) — closes four findings
   at once: (a) size-capped response read (`HTTP_MAX_RESPONSE_BYTES`, 16 MB) to
   stop multi-GB OOM; (b) `_NoRedirectHandler` refuses 3xx on authenticated
   POSTs, which previously forwarded `Authorization`/`x-api-key` across
   redirects and followed https→http downgrades; (c) the same handler removes
   the SSRF-via-redirect-to-internal-host vector.
4. **Cost-cap gaps** (`benchmark.py`) — the `--runs N` multiplier was missing
   from the Parallel estimate (10× silent overspend), and Tavily/Exa bypassed
   the guard entirely (it lived inside `if parallel:`). The guard now covers
   all three providers, multiplies by `len(targets) * runs`, and reports a
   per-provider breakdown on trip.
5. **FindAll poll-loop logic bug** (`parallel_workflows.py`) — on deadline
   expiry the loop still called `.result()` against an active job, writing
   partial data as success. Now raises `ParallelWorkflowError` on timeout, like
   `_wait_for_taskgroup_completion`.

### Eval integrity (published-number credibility)
6. **Freshness dimension** returned 100/100 (a free +15) for any target without
   `freshness_terms`; now returns a neutral 65 with a visible signal. The eight
   published targets all set terms, so reference numbers are unaffected — this
   only de-inflates ad-hoc single-target runs.
7. **`_aggregate_runs` wall time** took the median over *all* runs including
   fast failures (a broken case could report 0.1 s), contradicting its own
   docstring; now medians over successful runs only.
8. **`source_scoring` substring false positives** — `"developer" in domain` and
   `"/api" in path` rewarded `notadeveloper.com`, `/apiary/…`. Domain check is
   now subdomain-anchored; path check matches a whole segment or a
   `-`/`_`-prefixed one (so `/api-reference` and `/api/v2` still score, but
   `/apiary` does not). Verified against all 819 source rows in `.bench/runs/`:
   **0 change** — the only real effect is excluding genuine false positives,
   which do not appear in the published corpus. (A first cut using
   segment-*exact* matching wrongly dropped 163 `/api-reference` rows by −10;
   that regression was caught by re-scoring the stored runs and corrected.)

### Hygiene (low severity, defensive)
- Recipe size guard (`MAX_RECIPE_BYTES`, 1 MB) before `yaml.safe_load`
  (billion-laughs).
- stdin API-key length cap (512 chars).
- `_redact_secret_like` strips token-shaped substrings from third-party error
  bodies before they reach `benchmark.report.json` / Raindrop traces.
- Raindrop traces now send `output_dir.name` / artifact basenames instead of
  absolute home paths.
- Removed the dead/misleading redaction branch in `_load_mcp_servers`.
- `_cap_fixture_content` bounds imported-fixture `full_content`/`excerpts` to
  the live `DEFAULT_MAX_FULL_CONTENT_CHARS`.
- SSRF/artifact hygiene: `run_live_context_pack` now runs provider URLs through
  `UrlValidator` (https-only) before extract, matching the extract-pack path.

### Incidental
- Fixed a pre-existing mypy error in `_workload_disclosure_lines` (`med` typed
  int then assigned `""`), introduced by commit `a2a8535`.
- Annotated the pre-existing B311 jitter finding in `_retry_delay_seconds` with
  a policy-compliant `# nosec B311` (non-crypto retry backoff). Bandit was red
  at HEAD on this; it is now green.

## Refuted (verified false positives)
- `_resolve_recipe_path` arbitrary read — trust boundary is "ran an untrusted
  file"; `url_file` content is https-validated, `diff` reads only a fixed name.
- `_safe_slug` — genuinely neutralizes path separators.
- benchmark argparse bare `type=int/float` — post-parse `_validate_positive_int`
  already rejects zero/negative.
- "Unbounded Retry-After" — the cap is applied at parse time (`min(..., CAP)`).
- Provider text in the published article — the Targets section is built only
  from hardcoded/user-controlled `_BenchmarkTarget` fields, not provider data.

## Verification
`ruff check` ✅ · `ruff format` ✅ · `mypy src` ✅ · `pytest tests` ✅ 476 passed ·
`bandit -c pyproject.toml -r src` ✅ exit 0 · `pip-audit` ✅ no known vulns.
Diff: 3 files, +201/−54.

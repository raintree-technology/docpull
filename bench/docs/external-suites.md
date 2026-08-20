# External benchmark lanes

External suites remain separate from the owned fixed-URL benchmark because
they measure different product contracts and can have independent governance,
judges, credentials, and cost profiles. They are experimental compatibility
probes, not benchmark claim sources.

## WANDR / Harbor

WANDR measures wide and deep research agents. It does not directly measure a
fixed-URL extractor, so its scores must never be merged into the `extract`
leaderboard. Its judge-based verifier also conflicts with the lab's
deterministic-only policy, so DocPull does not implement a WANDR score. The
upstream repository is pinned in `wandr/lock.json`; no fork or
vendored task data is required.

Prepare the pinned checkout and run its content-free consistency checks:

```bash
bench/experimental/external-suites/wandr/check.sh check
```

Check whether the cheapest local Docker smoke run is runnable:

```bash
bench/experimental/external-suites/wandr/check.sh preflight
```

The smoke workflow requires OpenAI and Perplexity credentials and still makes
paid solver, fetch, and judge calls. WANDR's upstream runner has no total spend
ceiling, so this repository intentionally does not wrap or auto-run its paid
commands. The compatibility probe never invokes a solver or judge.

The 2026-07-14 upstream check passed at commit
`67c56475463baad6d8998657f798756ea1f80d4d`. Its local smoke preflight stopped
before any paid call because `OPENAI_API_KEY` and `PERPLEXITY_API_KEY` were not
configured. See `wandr/status.json`.

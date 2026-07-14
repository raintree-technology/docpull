# Evaluation program

The program answers lane-specific product questions with deterministic evidence.
It deliberately avoids a cross-capability leaderboard.

## Decision policy

1. Controlled correctness is the blocking signal.
2. Persistent gaps are retained and prioritized; they are not silently removed.
3. Performance is compared only within matching environment and cache classes.
4. Live-web and hosted-provider changes are advisory until a protected manual
   run supplies fresh gold, credentials, and an explicit spend ceiling.
5. Statistical output emphasizes paired effect sizes, Wilson intervals,
   pass@k/pass^k, stability, exact McNemar tests, and Holm correction. Failure to
   reject a null hypothesis is never described as equivalence.

## Schema v2

Every case has discriminated lane-specific input and expectation types. Every
adapter declares capabilities and emits one discriminated observation payload.
The canonical scorer emits required assertion outcomes plus diagnostic metrics.
Portable reports store that output without recomputation and without raw bodies.

Run manifests bind results to suite, fixtures, protocol, dependency lock,
repository revision/dirty state, adapter configuration, OS/architecture,
environment, network isolation, cache, retry, pricing, concurrency, and a
sanitized command.

## Release gates

- Schema, freshness, and fixture-hash validation
- Ruff and mypy
- Unit and adapter contract tests
- Deterministic fixture regeneration
- Full 212-case controlled replay under network isolation
- Real public-CLI lifecycle and focused parse checks
- Explicit baseline check
- Root `bun run validate:full`
- Optional pinned WANDR consistency check with zero paid calls

Historical measured bundles live under `results/provisional/`. Their bytes and
original hashes are retained, but the namespace watermark says they are not
current evidence and are not for marketing.

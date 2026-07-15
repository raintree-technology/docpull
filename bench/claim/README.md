# Public-claim gate

Internal benchmark data and public comparative claims have different evidence
requirements. `docpull-bench claim check` is the fail-closed boundary between
them. It verifies evidence; it never generates claim language.

The default policy requires, independently for every claimed lane:

- at least two distinct compared systems;
- at least 100 cases, including 30 never-published held cases;
- at least five families, no family above 25%, and at least 95% unique inputs;
- at least ten domains for a live-web lane;
- five trials and at least 95% operational success for every compared system;
- at least 20 paired discordant cases per system pair, so a difference claim is
  not driven by an information-starved exact test;
- current gold, a signed encrypted holdout seal, and two independent reviewers
  from different non-owner organizations covering every case;
- signed, current protocol attestations matching each adapter configuration
  hash; and
- signed reconciliation to provider API usage, an invoice, or a dashboard for
  every report with provider cost.

The default policy accepts only detached GPG signatures from explicitly trusted
fingerprints and verifies each signature over canonical attestation JSON.
Sigstore remains reserved in the schema but is not enabled until a bundle and
identity verifier is implemented. A name or signature filename in YAML is not
treated as independent review. The example evidence file contains zero hashes
and cannot pass a real check; the release owner must add reviewer fingerprints
to a reviewed policy copy.

```bash
uv run --project bench --locked docpull-bench claim check \
  bench/cases/CLAIM_SUITE.yaml REPORT... \
  --policy bench/claim/policy-v1.yaml \
  --evidence /path/to/signed-evidence.yaml \
  --output claim-readiness.json --markdown CLAIM-READINESS.md
```

The committed 32-case extract, eight-case crawl, and 30-case search suites are
exploratory inputs. Repeating or mechanically paraphrasing them does not create
independent samples. They remain useful for product diagnosis but cannot pass
this gate. New 100-case sets must be assembled through blinded intake and
reviewed outside the DocPull authoring team. Only development cases, held-case
IDs, and a cryptographic commitment may be committed before opening the
holdout; held inputs and expectations stay encrypted with an external
custodian.

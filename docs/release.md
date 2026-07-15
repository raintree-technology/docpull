# Release Runbook

Use the guarded helper instead of hand-typing merge/tag commands.

Prerequisite: run these commands with Python 3.11 or newer.

## Prepare a Release PR

Work on a release branch, commit the release changes, then run:

```bash
make release-pr VERSION=6.0.0
```

This pushes the branch, opens a PR into `main`, and enables squash auto-merge.
It refuses to run directly from `main`.

## Final Local Verification

Before opening the release PR or cutting a tag, run the same local checks that
protect the package and generated release metadata:

```bash
make metadata-check
make lint
make test-all-local
make test-inventory
git diff --check
```

Then rebuild the distribution from a clean `dist/`, validate package metadata,
and smoke-install the wheel:

```bash
rm -rf dist .pkg-smoke
python -m pip install -r requirements-release.txt
python scripts/build_release.py --verify-reproducible
python -m twine check dist/*
python -m venv .pkg-smoke
.pkg-smoke/bin/python -m pip install dist/*.whl
.pkg-smoke/bin/docpull --version
```

Remove `.pkg-smoke/` after the smoke check. Do not publish from local artifacts;
the publish workflow rebuilds and uploads from the tagged commit.

## Publish After Merge

After the PR merges:

```bash
git switch main
git pull --ff-only origin main
make release-publish VERSION=6.0.0
```

This verifies `origin/main` has the requested `pyproject.toml` version, puts
`vX.Y.Z` on the merged `origin/main` commit, and pushes the tag to start the
PyPI workflow. After the PyPI trusted-publish step succeeds on a tag push, the
workflow creates or updates the GitHub Release for that tag and marks it as the
latest release. It uses `docs/release-post-vX.Y.md` when present and otherwise
falls back to generated notes.

Verify both public release surfaces:

```bash
gh release view v6.0.0 --json tagName,name,publishedAt,url
python - <<'PY'
import json, urllib.request
with urllib.request.urlopen("https://pypi.org/pypi/docpull/json", timeout=20) as r:
    print(json.load(r)["info"]["version"])
PY
```

The helper refuses to move an existing remote tag by default.

If a tag was pushed early and the publish workflow did not complete, first
confirm the version was not published on PyPI, then run:

```bash
make release-publish-replace-tag VERSION=6.0.0
```

## Manual Publish Fallback

If the tag push does not start Actions or the publish job needs to be rerun from
the merged `main` commit:

```bash
make release-dispatch VERSION=6.0.0
```

The workflow refuses manual dispatch from any branch other than `main`, and the
requested version must match `pyproject.toml`.

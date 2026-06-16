# Release Runbook

Use the guarded helper instead of hand-typing merge/tag commands.

## Prepare a Release PR

Work on a release branch, commit the release changes, then run:

```bash
make release-pr VERSION=4.4.0
```

This pushes the branch, opens a PR into `main`, and enables squash auto-merge.
It refuses to run directly from `main`.

## Publish After Merge

After the PR merges:

```bash
git switch main
git reset --hard origin/main
make release-publish VERSION=4.4.0
```

This verifies `origin/main` has the requested `pyproject.toml` version, puts
`vX.Y.Z` on the merged `origin/main` commit, and pushes the tag to start the
PyPI workflow. If a tag already points at an old pre-merge commit, the helper
replaces it only when `--replace-tag` is used through the Makefile target.

## Manual Publish Fallback

If the tag push does not start Actions or the publish job needs to be rerun from
the merged `main` commit:

```bash
make release-dispatch VERSION=4.4.0
```

The workflow refuses manual dispatch from any branch other than `main`, and the
requested version must match `pyproject.toml`.

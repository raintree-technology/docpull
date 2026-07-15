#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LOCK="$SCRIPT_DIR/lock.json"
CACHE_ROOT="${DOCPULL_BENCH_EXTERNAL_DIR:-$BENCH_ROOT/.external}"
CHECKOUT="$CACHE_ROOT/wandr"
REPOSITORY="$(sed -n 's/.*"repository": *"\([^"]*\)".*/\1/p' "$LOCK")"
COMMIT="$(sed -n 's/.*"commit": *"\([^"]*\)".*/\1/p' "$LOCK")"

if [[ -z "$REPOSITORY" || -z "$COMMIT" ]]; then
  printf 'error: invalid WANDR lock file: %s\n' "$LOCK" >&2
  exit 2
fi

mkdir -p "$CACHE_ROOT"
if [[ ! -d "$CHECKOUT/.git" ]]; then
  git clone --filter=blob:none --no-checkout "$REPOSITORY" "$CHECKOUT"
fi
git -C "$CHECKOUT" fetch --depth 1 origin "$COMMIT"
git -C "$CHECKOUT" checkout --detach --force "$COMMIT"

case "${1:-check}" in
  check)
    exec "$CHECKOUT/scripts/wandr" check
    ;;
  preflight)
    exec "$CHECKOUT/scripts/wandr" preflight local smoke
    ;;
  *)
    printf 'usage: bench/experimental/external-suites/wandr/check.sh [check|preflight]\n' >&2
    exit 2
    ;;
esac

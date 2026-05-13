#!/bin/bash
#
# Smoke tests for lib/instructions_pipeline.sh (build_instruction_bundle).
# Run: cd .github/actions/regression-handling/regression_handling && ./tests/lib/instructions_pipeline_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-handling/regression_handling"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export REGRESSION_HANDLING_ROOT="$AT_ROOT"
# shellcheck source=../../lib/instructions_pipeline.sh
source "$AT_ROOT/lib/instructions_pipeline.sh"

echo "=== lib/instructions_pipeline.sh ==="

ROOT=$(mktemp -d)
OUT=$(mktemp)
trap 'rm -rf "$ROOT" "$OUT"' EXIT

mkdir -p "$ROOT/parts"
printf 'alpha\n' >"$ROOT/parts/one.txt"
printf 'beta\n' >"$ROOT/parts/two.txt"
{
    echo '# leading comment'
    echo ''
    echo 'parts/one.txt'
    echo 'parts/two.txt  '
} >"$ROOT/bundle.manifest"

assert "build_instruction_bundle succeeds" build_instruction_bundle "$OUT" "$ROOT" "bundle.manifest"
printf 'alpha\nbeta\n' >"$ROOT/expected.txt"
assert "concat order and whitespace-trimmed paths" cmp -s "$OUT" "$ROOT/expected.txt"

echo 'parts/missing.txt' >"$ROOT/bad.manifest"
assert_fails "missing fragment errors" build_instruction_bundle "$OUT" "$ROOT" "bad.manifest"

assert_eq "AT_PIPELINE_FILTER_FRAGMENTS path" "$AT_PIPELINE_FILTER_FRAGMENTS" "instructions/pipelines/filter.fragments"

trap - EXIT
rm -rf "$ROOT" "$OUT"

test_summary

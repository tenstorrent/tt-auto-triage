#!/bin/bash
#
# Tests for filter_triage.sh
#
# Run: cd .github/actions/regression-handling/regression_handling && ./tests/modules/analysis/filter_triage_test.sh
#
# Note: Integration test (full filter run) skipped - requires boundary artifacts and Copilot.
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-handling/regression_handling"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
FILTER_TRIAGE="$AT_ROOT/filter_triage.sh"

echo "=== filter_triage.sh ==="

# -- missing args -------------------------------------------------------------
set +e
out=$(bash "$FILTER_TRIAGE" 2>&1)
rc=$?
set -e
assert "missing args exits non-zero" [ "$rc" -ne 0 ]
assert "missing args prints usage" grep -q "Usage" <<< "$out"

# -- one arg (still missing subjob) --------------------------------------------
set +e
out=$(bash "$FILTER_TRIAGE" "workflow" 2>&1)
rc=$?
set -e
assert "one arg exits non-zero" [ "$rc" -ne 0 ]

# -- commit de-duplication logic (unit test) ------------------------------------
# Simulate the jq de-dup: unique_by(.commit // .commit_short // .commit_sha // "")
tmp_json=$(mktemp)
trap 'rm -f "$tmp_json"' EXIT
echo '[{"commit":"a"},{"commit":"a","commit_short":"a"},{"commit":"b"}]' > "$tmp_json"
deduped=$(jq 'unique_by(.commit // .commit_short // .commit_sha // "")' "$tmp_json")
count=$(echo "$deduped" | jq 'length')
assert "unique_by deduplicates commits" [ "$count" -eq 2 ]

test_summary

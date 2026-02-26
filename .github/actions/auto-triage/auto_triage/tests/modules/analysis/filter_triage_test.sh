#!/bin/bash
#
# Tests for filter_triage.sh
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/analysis/filter_triage_test.sh
#
# Note: Integration test (full filter run) skipped - requires boundary artifacts and Copilot.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
FILTER_TRIAGE="$ROOT_DIR/filter_triage.sh"

_d="$(cd "$SCRIPT_DIR" && pwd)"
while [ "$_d" != "/" ]; do [ -f "$_d/testing_lib_files/test_harness.sh" ] && . "$_d/testing_lib_files/test_harness.sh" && break; _d="${_d%/*}"; done
export AUTO_TRIAGE_ROOT="$ROOT_DIR"

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

#!/bin/bash
#
# Tests for auto_triage.sh
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/analysis/auto_triage_test.sh
#
# Note: Integration test (full triage run) skipped - requires boundary artifacts and Copilot.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
AUTO_TRIAGE="$ROOT_DIR/auto_triage.sh"

source "$SCRIPT_DIR/../../lib/test_harness.sh"
export AUTO_TRIAGE_ROOT="$ROOT_DIR"

echo "=== auto_triage.sh ==="

# -- missing args -------------------------------------------------------------
set +e
out=$(bash "$AUTO_TRIAGE" 2>&1)
rc=$?
set -e
assert "missing args exits non-zero" [ "$rc" -ne 0 ]
assert "missing args prints usage" grep -q "Usage" <<< "$out"

# -- one arg (still missing subjob) --------------------------------------------
set +e
out=$(bash "$AUTO_TRIAGE" "workflow" 2>&1)
rc=$?
set -e
assert "one arg exits non-zero" [ "$rc" -ne 0 ]

# -- boundary artifact validation (jq logic) -----------------------------------
# subjob_runs can be array or {runs: [...]}
tmp_json=$(mktemp)
trap 'rm -f "$tmp_json"' EXIT
echo '[{"status":"success"},{"status":"failure"}]' > "$tmp_json"
fail_count=$(jq 'if type=="array" then ([.[] | select(.status != "success")] | length) else ((.runs // []) | map(select(.status != "success")) | length) end' "$tmp_json")
assert "jq counts failures in array" [ "$fail_count" -eq 1 ]

echo '{"runs":[{"status":"success"},{"status":"failure"},{"status":"failure"}]}' > "$tmp_json"
fail_count=$(jq 'if type=="array" then ([.[] | select(.status != "success")] | length) else ((.runs // []) | map(select(.status != "success")) | length) end' "$tmp_json")
assert "jq counts failures in runs object" [ "$fail_count" -eq 2 ]

test_summary

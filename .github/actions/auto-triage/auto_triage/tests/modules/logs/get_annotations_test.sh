#!/bin/bash
#
# Tests for get_annotations.sh
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/logs/get_annotations_test.sh
#
# Note: Integration tests (real API) are skipped unless GH_TOKEN is set and valid.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
GET_ANNOTATIONS="$ROOT_DIR/get_annotations.sh"

source "$SCRIPT_DIR/../../lib/test_harness.sh"
export AUTO_TRIAGE_ROOT="$ROOT_DIR"

echo "=== get_annotations.sh ==="

# -- invalid URL --------------------------------------------------------------
set +e
out=$(bash "$GET_ANNOTATIONS" "https://example.com/not/a/job" 2>&1)
rc=$?
set -e
assert "invalid URL exits non-zero" [ "$rc" -ne 0 ]
assert "invalid URL prints error" echo "$out" | grep -q "Unable to parse"

# -- missing argument ---------------------------------------------------------
set +e
out=$(bash "$GET_ANNOTATIONS" 2>&1)
rc=$?
set -e
assert "missing arg exits non-zero" [ "$rc" -ne 0 ]
assert "missing arg prints usage" echo "$out" | grep -q "Usage"

# -- valid URL format, non-existent job (produces empty annotations) ----------
# Uses real API when GH_TOKEN is available; harmless when not.
url="https://github.com/tenstorrent/tt-metal/actions/runs/1/job/999999999999"
tmp_out=$(mktemp)
trap "rm -f $tmp_out" EXIT
if bash "$GET_ANNOTATIONS" "$url" "$tmp_out" 2>/dev/null; then
    assert "valid URL format runs without crash" [ -f "$tmp_out" ]
    content=$(cat "$tmp_out")
    assert "output is JSON array" jq -e 'type == "array"' <<<"$content" >/dev/null
else
    # API may fail without token or for non-existent job - skip assertion
    :
fi

test_summary

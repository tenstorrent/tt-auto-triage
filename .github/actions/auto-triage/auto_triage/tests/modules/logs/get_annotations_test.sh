#!/bin/bash
#
# Tests for get_annotations.sh
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/logs/get_annotations_test.sh
#
# Note: Integration tests (real API) are skipped unless GH_TOKEN is set and valid.
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
GET_ANNOTATIONS="$AT_ROOT/get_annotations.sh"

echo "=== get_annotations.sh ==="

# -- invalid URL --------------------------------------------------------------
set +e
out=$(bash "$GET_ANNOTATIONS" "https://example.com/not/a/job" 2>&1)
rc=$?
set -e
assert "invalid URL exits non-zero" [ "$rc" -ne 0 ]
assert "invalid URL prints error" grep -q "Unable to parse" <<< "$out"

# -- missing argument ---------------------------------------------------------
set +e
out=$(bash "$GET_ANNOTATIONS" 2>&1)
rc=$?
set -e
assert "missing arg exits non-zero" [ "$rc" -ne 0 ]
assert "missing arg prints usage" grep -q "Usage" <<< "$out"

# -- valid URL format, non-existent job (produces empty annotations) ----------
# Uses real API when GH_TOKEN is available; harmless when not.
url="https://github.com/tenstorrent/tt-metal/actions/runs/1/job/999999999999"
if ! tmp_out=$(mktemp); then
    echo "Failed to create temporary file" >&2
    exit 1
fi
trap 'rm -f "$tmp_out"' EXIT
if ! command -v gh >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
    echo "Skipping valid URL integration test: gh and/or jq not available"
else
    set +e
    bash "$GET_ANNOTATIONS" "$url" "$tmp_out" 2>/dev/null
    rc=$?
    set -e
    assert "valid URL format exits zero when prerequisites present" [ "$rc" -eq 0 ]
    assert "valid URL format runs without crash" [ -f "$tmp_out" ]
    content=$(cat "$tmp_out")
    assert "output is JSON array" [ "$(echo "$content" | jq -r 'type')" = "array" ]
fi

test_summary

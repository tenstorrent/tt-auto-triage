#!/bin/bash
#
# Unit tests for modules/retry/result_comparator.sh
#
# Uses a mock copilot CLI to avoid invoking real Copilot.
# Run: bash tests/modules/retry/result_comparator_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"

# -- create mock copilot ------------------------------------------------------
MOCK_DIR=$(mktemp -d)
trap 'rm -rf "$MOCK_DIR"' EXIT
cat > "$MOCK_DIR/copilot" <<'MOCK'
#!/bin/bash
# When RESULT_COMPARATOR_DATA_DIR is set (by run_copilot_error_comparison),
# write a known error_comparison.json for testing.
if [ -n "${RESULT_COMPARATOR_DATA_DIR:-}" ] && [ -d "${RESULT_COMPARATOR_DATA_DIR}" ]; then
    echo '{"same_failure": true, "retry_error_extracted": "Mock extracted error"}' \
        > "${RESULT_COMPARATOR_DATA_DIR}/error_comparison.json"
fi
exit 0
MOCK
chmod +x "$MOCK_DIR/copilot"
export PATH="$MOCK_DIR:$PATH"

source "$AT_ROOT/modules/retry/result_comparator.sh"

echo "=== modules/retry/result_comparator.sh ==="

# -- get_same_failure_from_comparison ------------------------------------------
assert_eq "get_same_failure: missing file" "$(get_same_failure_from_comparison "")" "false"
assert_eq "get_same_failure: nonexistent file" "$(get_same_failure_from_comparison /nonexistent)" "false"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$MOCK_DIR" "$TMP_DIR"' EXIT
echo '{"same_failure": true}' > "$TMP_DIR/same.json"
echo '{"same_failure": false}' > "$TMP_DIR/diff.json"
echo '{}' > "$TMP_DIR/empty.json"

assert_eq "get_same_failure: same" "$(get_same_failure_from_comparison "$TMP_DIR/same.json")" "true"
assert_eq "get_same_failure: different" "$(get_same_failure_from_comparison "$TMP_DIR/diff.json")" "false"
assert_eq "get_same_failure: empty json defaults false" "$(get_same_failure_from_comparison "$TMP_DIR/empty.json")" "false"

# -- get_retry_error_extracted --------------------------------------------------
echo '{"retry_error_extracted": "AssertionError: max mismatch"}' > "$TMP_DIR/with_extracted.json"
assert_eq "get_retry_error_extracted: has value" "$(get_retry_error_extracted "$TMP_DIR/with_extracted.json")" "AssertionError: max mismatch"
assert_eq "get_retry_error_extracted: missing file" "$(get_retry_error_extracted "")" ""

# -- determine_retry_result ----------------------------------------------------
assert_eq "determine_retry_result: retry success -> passed" "$(determine_retry_result "success" "true")" "passed"
assert_eq "determine_retry_result: retry success ignores same_failure" "$(determine_retry_result "success" "false")" "passed"
assert_eq "determine_retry_result: failed + same -> failed_same" "$(determine_retry_result "failure" "true")" "failed_same"
assert_eq "determine_retry_result: failed + different -> failed_different" "$(determine_retry_result "failure" "false")" "failed_different"
assert_eq "determine_retry_result: default same_failure false" "$(determine_retry_result "failure" "")" "failed_different"

# -- run_copilot_error_comparison (with mock) ----------------------------------
mkdir -p "$TMP_DIR/root/auto_triage/data"
echo "original error" > "$TMP_DIR/root/auto_triage/data/original_error.txt"
echo "retry error" > "$TMP_DIR/root/auto_triage/data/retry_error.txt"
mkdir -p "$TMP_DIR/root"
echo "Fake instructions for Copilot" > "$TMP_DIR/root/compare_errors_instructions.txt"

run_copilot_error_comparison "$TMP_DIR/root" "$TMP_DIR/root/auto_triage/data" || true
comparison_file="$TMP_DIR/root/auto_triage/data/error_comparison.json"
assert_eq "run_copilot_error_comparison: produces error_comparison.json" "$([ -f "$comparison_file" ] && echo "exists" || echo "missing")" "exists"
same=$(get_same_failure_from_comparison "$TMP_DIR/root/auto_triage/data/error_comparison.json")
assert_eq "run_copilot_error_comparison: mock writes same_failure=true" "$same" "true"
extracted=$(get_retry_error_extracted "$TMP_DIR/root/auto_triage/data/error_comparison.json")
assert_eq "run_copilot_error_comparison: mock writes retry_error_extracted" "$extracted" "Mock extracted error"

test_summary

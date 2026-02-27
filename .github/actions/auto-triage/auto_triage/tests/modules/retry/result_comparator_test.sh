#!/bin/bash
#
# Unit tests for modules/retry/result_comparator.sh
#
# Run: bash tests/modules/retry/result_comparator_test.sh
# Or:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/retry/result_comparator_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"

source "$AT_ROOT/modules/retry/result_comparator.sh"

echo "=== modules/retry/result_comparator.sh ==="

# -- compare_errors -----------------------------------------------------------
# Empty inputs -> 0
score=$(compare_errors "" "something")
assert_eq "compare_errors: empty original returns 0" "$score" "0"
score=$(compare_errors "something" "")
assert_eq "compare_errors: empty retry returns 0" "$score" "0"

# Identical errors -> high score (substring/containment returns 85)
orig="FAILED tests/ttnn/test_ops.py::test_reduce_max - AssertionError: max mismatch"
score=$(compare_errors "$orig" "$orig")
assert_eq "compare_errors: identical errors score high" "$score" "85"

# Substring containment -> high score
short="AssertionError: max mismatch expected=0.0"
long="FAILED tests/ttnn/test_ops.py::test_reduce_max - AssertionError: max mismatch expected=0.0 actual=0.125"
score=$(compare_errors "$short" "$long")
assert_eq "compare_errors: substring containment returns 85" "$score" "85"

# Different errors -> low score (no overlap)
orig="AssertionError in test_reduce_max: max mismatch"
retry="TimeoutError: Job exceeded maximum runtime of 3600 seconds"
score=$(compare_errors "$orig" "$retry")
assert_eq "compare_errors: different errors score low" "$score" "0"

# Similar errors (same test name, overlapping words) -> moderate-high
orig="FAILED test_ops::test_reduce_max AssertionError max mismatch"
retry="FAILED test_ops::test_reduce_max AssertionError expected 0.0 got 0.125"
score=$(compare_errors "$orig" "$retry")
# Word overlap: shared words -> score ~60-70; must be at least 40
assert_eq "compare_errors: similar errors score moderate" "$([ "$score" -ge 40 ] && echo pass || echo fail)" "pass"

# -- determine_retry_result ---------------------------------------------------
# Retry passed -> passed
result=$(determine_retry_result "failure" "success" "0")
assert_eq "determine_retry_result: retry success -> passed" "$result" "passed"

# Retry failed, high similarity -> failed_same
result=$(determine_retry_result "failure" "failure" "85")
assert_eq "determine_retry_result: high similarity -> failed_same" "$result" "failed_same"

# Retry failed, low similarity -> failed_different
result=$(determine_retry_result "failure" "failure" "20")
assert_eq "determine_retry_result: low similarity -> failed_different" "$result" "failed_different"

# Boundary: exactly at threshold (70) -> failed_same
result=$(determine_retry_result "failure" "failure" "70")
assert_eq "determine_retry_result: threshold 70 -> failed_same" "$result" "failed_same"

# Just below threshold -> failed_different
result=$(determine_retry_result "failure" "failure" "69")
assert_eq "determine_retry_result: below threshold -> failed_different" "$result" "failed_different"

# Custom threshold via env
RESULT_COMPARATOR_SAME_THRESHOLD=50 result=$(determine_retry_result "failure" "failure" "55")
assert_eq "determine_retry_result: custom threshold 50, score 55 -> failed_same" "$result" "failed_same"

test_summary

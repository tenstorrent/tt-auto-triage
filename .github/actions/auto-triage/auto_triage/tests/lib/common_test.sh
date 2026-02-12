#!/bin/bash
#
# Basic smoke tests for lib/common.sh
# Run from auto_triage/ directory: ./tests/lib/common_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_TRIAGE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIB_DIR="$AUTO_TRIAGE_DIR/lib"

export AUTO_TRIAGE_ROOT="$AUTO_TRIAGE_DIR"
source "$LIB_DIR/common.sh"

TESTS_PASSED=0
TESTS_FAILED=0

run_test() {
    local name="$1"
    shift
    if "$@"; then
        echo "  OK: $name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        echo "  FAIL: $name"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

echo "=== lib/common.sh smoke tests ==="

run_test "root is set" test -n "${AUTO_TRIAGE_ROOT:-}"

run_test "get_data_dir" test -n "$(get_data_dir)" -a "$(get_data_dir)" = "${AUTO_TRIAGE_ROOT}/auto_triage/data"

run_test "get_output_dir" test -n "$(get_output_dir)" -a "$(get_output_dir)" = "${AUTO_TRIAGE_ROOT}/auto_triage/output"

run_test "get_logs_dir" test -n "$(get_logs_dir)" -a "$(get_logs_dir)" = "${AUTO_TRIAGE_ROOT}/auto_triage/logs"

run_test "get_data_dir with explicit root" test "$(get_data_dir /tmp/foo)" = "/tmp/foo/auto_triage/data"

run_test "log functions" eval 'log_info x >/dev/null && log_error x 2>/dev/null && log_warn x 2>/dev/null && log_success x >/dev/null'

run_test "check_command exists" check_command bash

run_test "check_command missing" eval '! (check_command __nonexistent_cmd_xyz_xyz 2>/dev/null)'

unset __TEST_UNSET_VAR 2>/dev/null || true
run_test "get_env_with_default unset" test "$(get_env_with_default __TEST_UNSET_VAR "default")" = "default"

export __TEST_SET_VAR="actual"
run_test "get_env_with_default set" test "$(get_env_with_default __TEST_SET_VAR "default")" = "actual"
unset __TEST_SET_VAR

# die exits with 1 (run in subshell since die exits)
if ( die "test" 2>/dev/null ); then
    echo "  FAIL: die should exit 1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
else
    [ $? -eq 1 ] && { echo "  OK: die exits with 1"; TESTS_PASSED=$((TESTS_PASSED + 1)); } || { echo "  FAIL: die exit code"; TESTS_FAILED=$((TESTS_FAILED + 1)); }
fi

if command -v jq >/dev/null 2>&1; then
    TMP_JSON=$(mktemp)
    echo '{"key":"value"}' > "$TMP_JSON"
    run_test "json_get" test "$(json_get .key "$TMP_JSON" "x")" = "value"
    run_test "json_get default" test "$(json_get .missing "$TMP_JSON" "fallback")" = "fallback"
    rm -f "$TMP_JSON"
else
    echo "  SKIP: json tests (jq not installed)"
fi

echo ""
echo "=== Results: $TESTS_PASSED passed, $TESTS_FAILED failed ==="

[ $TESTS_FAILED -eq 0 ]

#!/bin/bash
#
# Smoke tests for lib/common.sh
# Run:  cd .github/actions/regression-handling/regression_handling && ./tests/lib/common_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-handling/regression_handling"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export REGRESSION_HANDLING_ROOT="$AT_ROOT"
source "$AT_ROOT/lib/common.sh"
echo "=== lib/common.sh ==="

# -- root detection -----------------------------------------------------------
assert "REGRESSION_HANDLING_ROOT is set" test -n "$REGRESSION_HANDLING_ROOT"

# -- path helpers -------------------------------------------------------------
assert_eq "get_data_dir"            "$(get_data_dir)"            "$REGRESSION_HANDLING_ROOT/regression_handling/data"
assert_eq "get_output_dir"          "$(get_output_dir)"          "$REGRESSION_HANDLING_ROOT/regression_handling/output"
assert_eq "get_logs_dir"            "$(get_logs_dir)"            "$REGRESSION_HANDLING_ROOT/regression_handling/logs"
assert_eq "get_data_dir custom root" "$(get_data_dir /tmp/foo)"  "/tmp/foo/regression_handling/data"

# -- logging (just verifying no crash) ----------------------------------------
assert "log_info"    eval 'log_info    "msg" >/dev/null'
assert "log_success" eval 'log_success "msg" >/dev/null'
assert "log_warn"    eval 'log_warn    "msg" 2>/dev/null'
assert "log_error"   eval 'log_error   "msg" 2>/dev/null'

# -- error handling -----------------------------------------------------------
assert       "check_command (exists)"  check_command bash
assert_fails "check_command (missing)" check_command __no_such_cmd_abc123
assert_fails "die exits"               die "deliberate"

assert "warn does not exit" eval 'warn "harmless" 2>/dev/null; true'

# -- env helpers --------------------------------------------------------------
unset __T_UNSET 2>/dev/null || true
assert_eq "get_env_with_default (unset)" "$(get_env_with_default __T_UNSET fallback)" "fallback"

export __T_SET="hello"
assert_eq "get_env_with_default (set)"   "$(get_env_with_default __T_SET fallback)"   "hello"
unset __T_SET

# -- JSON helpers (skip if jq absent) ----------------------------------------
if command -v jq >/dev/null 2>&1; then
    _tmp=$(mktemp)
    echo '{"name":"triage","count":42,"null":null}' > "$_tmp"

    assert_eq "json_get existing key"  "$(json_get .name  "$_tmp" x)"      "triage"
    assert_eq "json_get missing key"   "$(json_get .nope  "$_tmp" dflt)"   "dflt"
    assert_eq "json_get null key"      "$(json_get .null  "$_tmp" none)"   "none"
    assert_eq "jq_safe"                "$(jq_safe -r .name "$_tmp")"       "triage"
    assert_fails "jq_safe missing file" jq_safe -r .name "/no/such/file.json"

    rm -f "$_tmp"
else
    echo "  SKIP  json tests (jq not installed)"
fi

# -- summary ------------------------------------------------------------------
test_summary

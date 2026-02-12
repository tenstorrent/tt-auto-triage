#!/bin/bash
#
# Smoke tests for lib/common.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/lib/common_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/lib"

export AUTO_TRIAGE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$LIB_DIR/common.sh"

# -----------------------------------------------------------------------------
# Minimal test harness
# -----------------------------------------------------------------------------
_pass=0 _fail=0

assert() {                       # assert "description" <command...>
    local desc="$1"; shift
    if "$@" 2>/dev/null; then
        echo "  PASS  $desc"; _pass=$((_pass + 1))
    else
        echo "  FAIL  $desc"; _fail=$((_fail + 1))
    fi
}

assert_eq() {                    # assert_eq "description" "actual" "expected"
    local desc="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  PASS  $desc"; _pass=$((_pass + 1))
    else
        echo "  FAIL  $desc  (got '$actual', expected '$expected')"; _fail=$((_fail + 1))
    fi
}

assert_fails() {                 # assert_fails "description" <command...>
    local desc="$1"; shift
    if ! ("$@" 2>/dev/null); then
        echo "  PASS  $desc"; _pass=$((_pass + 1))
    else
        echo "  FAIL  $desc  (expected failure)"; _fail=$((_fail + 1))
    fi
}

echo "=== lib/common.sh ==="

# -- root detection -----------------------------------------------------------
assert "AUTO_TRIAGE_ROOT is set" test -n "$AUTO_TRIAGE_ROOT"

# -- path helpers -------------------------------------------------------------
assert_eq "get_data_dir"            "$(get_data_dir)"            "$AUTO_TRIAGE_ROOT/auto_triage/data"
assert_eq "get_output_dir"          "$(get_output_dir)"          "$AUTO_TRIAGE_ROOT/auto_triage/output"
assert_eq "get_logs_dir"            "$(get_logs_dir)"            "$AUTO_TRIAGE_ROOT/auto_triage/logs"
assert_eq "get_data_dir custom root" "$(get_data_dir /tmp/foo)"  "/tmp/foo/auto_triage/data"

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

export __T_REQ="ok"
assert "require_env (set)"   eval 'require_env __T_REQ'
unset __T_REQ
assert_fails "require_env (unset)" require_env __T_REQ

# -- JSON helpers (skip if jq absent) ----------------------------------------
if command -v jq >/dev/null 2>&1; then
    _tmp=$(mktemp)
    echo '{"name":"triage","count":42}' > "$_tmp"

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
echo ""
echo "=== $_pass passed, $_fail failed ==="
[ "$_fail" -eq 0 ]

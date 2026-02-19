#!/bin/bash
#
# Shared test harness for lib tests. Source from common_test.sh, config_test.sh, etc.
# Provides: assert, assert_eq, assert_fails, test_summary.
# Initializes _pass and _fail.
#

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

test_summary() {
    echo ""
    echo "=== $_pass passed, $_fail failed ==="
    [ "$_fail" -eq 0 ]
}

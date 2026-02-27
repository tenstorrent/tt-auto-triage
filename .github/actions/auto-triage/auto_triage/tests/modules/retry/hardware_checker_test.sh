#!/bin/bash
#
# Smoke tests for modules/retry/hardware_checker.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/retry/hardware_checker_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
source "$AT_ROOT/modules/retry/hardware_checker.sh"

echo "=== modules/retry/hardware_checker.sh ==="

# -- get_hardware_type ---------------------------------------------------------
assert_eq "get_hardware_type: N150"  "$(get_hardware_type "yolov5x-N150-func")"   "n150"
assert_eq "get_hardware_type: n150"  "$(get_hardware_type "some-n150-job")"      "n150"
assert_eq "get_hardware_type: N300" "$(get_hardware_type "workflow / N300-test")" "n300"
assert_eq "get_hardware_type: P100A" "$(get_hardware_type "p100a-unit-tests")"   "p100a"
assert_eq "get_hardware_type: P100"  "$(get_hardware_type "p100-func-tests")"     "p100a"
assert_eq "get_hardware_type: P150"  "$(get_hardware_type "resnet-P150-perf")"    "p150"
assert_eq "get_hardware_type: P300"  "$(get_hardware_type "P300-smoke")"          "p300"
assert_eq "get_hardware_type: unknown" "$(get_hardware_type "galaxy-frequent-tests")" "unknown"
assert_eq "get_hardware_type: unknown" "$(get_hardware_type "t3k-ttnn-tests")"    "unknown"
assert_eq "get_hardware_type: unknown (empty)" "$(get_hardware_type "")"         "unknown"

# -- is_hardware_supported -----------------------------------------------------
assert       "supported: N150"      is_hardware_supported "yolov5x-N150-func"
assert       "supported: N300"      is_hardware_supported "N300-unit-tests"
assert       "supported: P100A"     is_hardware_supported "p100a-func"
assert       "supported: P100"      is_hardware_supported "some-P100-job"
assert       "supported: P150"      is_hardware_supported "workflow / P150-test"
assert       "supported: P300"      is_hardware_supported "P300-smoke"

assert_fails "not supported: galaxy"       is_hardware_supported "galaxy-frequent-tests"
assert_fails "not supported: T3K"          is_hardware_supported "t3k-ttnn-tests"
assert_fails "not supported: T3000"        is_hardware_supported "t3000-unit-tests"
assert_fails "not supported: N150+galaxy"  is_hardware_supported "N150-galaxy-hybrid"
assert_fails "not supported: random"       is_hardware_supported "vanilla-unit-tests"
assert_fails "not supported: empty"        is_hardware_supported ""

# -- summary ------------------------------------------------------------------
test_summary

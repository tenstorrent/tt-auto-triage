#!/bin/bash
#
# Smoke tests for modules/retry/hardware_checker.sh
# Run:  cd .github/actions/regression-analysis/regression_analysis && ./tests/modules/retry/hardware_checker_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-analysis/regression_analysis"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
source "$AT_ROOT/modules/retry/hardware_checker.sh"

echo "=== modules/retry/hardware_checker.sh ==="

# -- is_hardware_supported -----------------------------------------------------
assert       "supported: N150"      is_hardware_supported "yolov5x-N150-func"
assert       "supported: N300"      is_hardware_supported "N300-unit-tests"
assert       "supported: P100A"     is_hardware_supported "p100a-func"
assert       "supported: P100a"      is_hardware_supported "some-P100-job"
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

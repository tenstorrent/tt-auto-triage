#!/bin/bash
#
# Smoke tests for modules/boundaries/run_processor.sh
# Run:  cd .github/actions/regression-analysis/regression_analysis && ./tests/modules/boundaries/run_processor_test.sh
#
# Tests module loading and is_commit_newer. process_workflow_runs requires
# network/gh auth and is exercised via find_boundaries.sh integration.
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-analysis/regression_analysis"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AT_OWNER_REPO="${AT_OWNER_REPO:-tenstorrent/tt-auto-triage}"
export REGRESSION_ANALYSIS_ROOT="$AT_ROOT"

# run_processor requires write_cancel_and_exit; use no-op for unit test
write_cancel_and_exit() {
    echo "CANCEL: $1" >&2
    exit 1
}
source "$AT_ROOT/modules/boundaries/run_processor.sh"

echo "=== modules/boundaries/run_processor.sh ==="

# -- process_workflow_runs exists ----------------------------------------------
assert "process_workflow_runs is defined" eval 'type process_workflow_runs >/dev/null 2>&1'

# -- is_commit_newer: same commit ----------------------------------------------
assert_fails "is_commit_newer: same commit" is_commit_newer "abc123" "abc123"

# -- is_commit_newer: empty ----------------------------------------------------
assert_fails "is_commit_newer: empty first" is_commit_newer "" "abc123"
assert_fails "is_commit_newer: empty second" is_commit_newer "abc123" ""

# -- is_commit_newer: parent/child (requires git) ------------------------------
# In a real repo, HEAD is newer than HEAD~1
if git rev-parse HEAD~1 >/dev/null 2>&1; then
    parent=$(git rev-parse HEAD~1)
    child=$(git rev-parse HEAD)
    assert "is_commit_newer: child > parent" is_commit_newer "$child" "$parent"
    assert_fails "is_commit_newer: parent not > child" is_commit_newer "$parent" "$child"
fi

test_summary

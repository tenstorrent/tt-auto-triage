#!/bin/bash
#
# Smoke tests for modules/boundaries/run_processor.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/boundaries/run_processor_test.sh
#
# Tests module loading and is_commit_newer. process_workflow_runs requires
# network/gh auth and is exercised via find_boundaries.sh integration.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_TRIAGE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
_d="$(cd "$SCRIPT_DIR" && pwd)"
while [ "$_d" != "/" ]; do [ -f "$_d/testing_lib_files/test_harness.sh" ] && . "$_d/testing_lib_files/test_harness.sh" && break; _d="${_d%/*}"; done

# run_processor requires write_cancel_and_exit; use no-op for unit test
write_cancel_and_exit() {
    echo "CANCEL: $1" >&2
    exit 1
}

export AT_OWNER_REPO="${AT_OWNER_REPO:-tenstorrent/tt-auto-triage}"
export AUTO_TRIAGE_ROOT
source "$AUTO_TRIAGE_ROOT/modules/boundaries/run_processor.sh"

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

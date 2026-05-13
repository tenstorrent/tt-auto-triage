#!/bin/bash
#
# Smoke tests for modules/boundaries/workflow_finder.sh
# Run:  cd .github/actions/regression-handling/regression_handling && ./tests/modules/boundaries/workflow_finder_test.sh
#
# Requires: gh CLI authenticated, network access to GitHub API
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-handling/regression_handling"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"

# Must export before sourcing workflow_finder (config loads then and uses AT_OWNER_REPO).
# Use tt-auto-triage for CI (GITHUB_TOKEN can only access the workflow's repo).
export AT_OWNER_REPO="${AT_OWNER_REPO:-tenstorrent/tt-auto-triage}"
source "$AT_ROOT/modules/boundaries/workflow_finder.sh"

echo "=== modules/boundaries/workflow_finder.sh ==="

# -- find_workflow_id: known workflow -------------------------------------------
# test-regression-handling-lib.yml exists in tt-auto-triage (this repo)
wf_id=$(find_workflow_id "test-regression-handling-lib")
assert "find_workflow_id: test-regression-handling-lib" [ -n "$wf_id" ]
# GitHub workflow IDs are numeric
assert "find_workflow_id: numeric ID" eval 'case "$(printf %s "$wf_id")" in (*[!0-9]*) false;; (*) true;; esac'

# -- find_workflow_id: not found -----------------------------------------------
empty=$(find_workflow_id "__nonexistent_workflow_xyz_123" 2>/dev/null) || true
assert "find_workflow_id: nonexistent returns empty" [ -z "${empty:-}" ]

# -- find_workflow_id: empty arg -----------------------------------------------
empty2=$(find_workflow_id "" 2>/dev/null) || true
assert "find_workflow_id: empty arg returns empty" [ -z "${empty2:-}" ]

test_summary

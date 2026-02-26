#!/bin/bash
#
# Smoke tests for modules/boundaries/workflow_finder.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/boundaries/workflow_finder_test.sh
#
# Requires: gh CLI authenticated, network access to GitHub API
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_TRIAGE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
_d="$(cd "$SCRIPT_DIR" && pwd)"
while [ "$_d" != "/" ]; do [ -f "$_d/testing_lib_files/test_harness.sh" ] && . "$_d/testing_lib_files/test_harness.sh" && break; _d="${_d%/*}"; done

# Use current repo for CI (GITHUB_TOKEN can only access the workflow's repo).
# Default to tt-auto-triage so tests pass in CI; override with AT_OWNER_REPO for local testing.
export AT_OWNER_REPO="${AT_OWNER_REPO:-tenstorrent/tt-auto-triage}"
source "$AUTO_TRIAGE_ROOT/modules/boundaries/workflow_finder.sh"

echo "=== modules/boundaries/workflow_finder.sh ==="

# -- find_workflow_id: known workflow -------------------------------------------
# test-auto-triage-lib.yml exists in tt-auto-triage (this repo)
wf_id=$(find_workflow_id "test-auto-triage-lib")
assert "find_workflow_id: test-auto-triage-lib" [ -n "$wf_id" ]
# GitHub workflow IDs are numeric
assert "find_workflow_id: numeric ID" eval 'case "$(printf %s "$wf_id")" in (*[!0-9]*) false;; (*) true;; esac'

# -- find_workflow_id: not found -----------------------------------------------
empty=$(find_workflow_id "__nonexistent_workflow_xyz_123" 2>/dev/null) || true
assert "find_workflow_id: nonexistent returns empty" [ -z "${empty:-}" ]

# -- find_workflow_id: empty arg -----------------------------------------------
empty2=$(find_workflow_id "" 2>/dev/null) || true
assert "find_workflow_id: empty arg returns empty" [ -z "${empty2:-}" ]

test_summary

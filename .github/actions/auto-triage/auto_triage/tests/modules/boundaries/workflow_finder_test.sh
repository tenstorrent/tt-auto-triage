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
source "$AUTO_TRIAGE_ROOT/tests/lib/test_harness.sh"

# Use tenstorrent/tt-metal (default) for real workflow resolution
export AT_OWNER_REPO="${AT_OWNER_REPO:-tenstorrent/tt-metal}"
source "$AUTO_TRIAGE_ROOT/modules/boundaries/workflow_finder.sh"

echo "=== modules/boundaries/workflow_finder.sh ==="

# -- find_workflow_id: known workflow -------------------------------------------
# single-card-demo-tests.yaml exists in tt-metal
wf_id=$(find_workflow_id "single-card-demo-tests")
assert "find_workflow_id: single-card-demo-tests" [ -n "$wf_id" ]
# GitHub workflow IDs are numeric
assert "find_workflow_id: numeric ID" eval 'case "$(printf %s "$wf_id")" in (*[!0-9]*) false;; (*) true;; esac'

# -- find_workflow_id: not found -----------------------------------------------
empty=$(find_workflow_id "__nonexistent_workflow_xyz_123" 2>/dev/null) || true
assert "find_workflow_id: nonexistent returns empty" [ -z "${empty:-}" ]

# -- find_workflow_id: empty arg -----------------------------------------------
empty2=$(find_workflow_id "" 2>/dev/null) || true
assert "find_workflow_id: empty arg returns empty" [ -z "${empty2:-}" ]

test_summary

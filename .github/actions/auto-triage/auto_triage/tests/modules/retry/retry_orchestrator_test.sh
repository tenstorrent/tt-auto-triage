#!/bin/bash
#
# Smoke tests for modules/retry/retry_orchestrator.sh
#
# Verifies orchestrator runs and produces expected outputs. Uses minimal
# mock data; full integration would require extensive gh/copilot mocking.
#
# Run: bash tests/modules/retry/retry_orchestrator_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"

echo "=== modules/retry/retry_orchestrator.sh (smoke) ==="

# Test 1: Wrapper script exists and accepts args
assert "scripts/retry_on_deterministic.sh exists" test -f "$AT_ROOT/scripts/retry_on_deterministic.sh"
assert "retry_orchestrator.sh exists" test -f "$AT_ROOT/modules/retry/retry_orchestrator.sh"

# Test 2: Root retry_on_deterministic.sh execs the script
content=$(cat "$AT_ROOT/retry_on_deterministic.sh")
assert_eq "root retry_on_deterministic execs scripts" "$(echo "$content" | grep -q exec && echo ok || echo fail)" "ok"

# Test 3: Orchestrator with no slack_message exits early (doesn't crash)
OUTPUT_DIR="$AT_ROOT/output"
mkdir -p "$OUTPUT_DIR"
[ -f "$OUTPUT_DIR/slack_message.json" ] && mv "$OUTPUT_DIR/slack_message.json" "$OUTPUT_DIR/slack_message.json.bak" || true
rc=0; bash "$AT_ROOT/retry_on_deterministic.sh" "N150-test" "wf" 2>/dev/null || rc=$?
[ -f "$OUTPUT_DIR/slack_message.json.bak" ] && mv "$OUTPUT_DIR/slack_message.json.bak" "$OUTPUT_DIR/slack_message.json" || true
assert_eq "orchestrator exits gracefully when no slack_message" "$rc" "0"

test_summary

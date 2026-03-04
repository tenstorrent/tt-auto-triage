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

# NOTE: This file intentionally provides only smoke / wiring tests for
# modules/retry/retry_orchestrator.sh:
#   - Verifies scripts exist and are executable.
#   - Verifies the root retry_on_deterministic.sh script execs the module.
#   - Verifies the orchestrator exits cleanly when slack_message.json is
#     absent (no crash / non-zero exit).
#
# The orchestrator itself contains additional complex logic that is *not*
# unit-tested here, including:
#   - Eligibility checks (case 1/4 detection, hardware validation,
#     duration checks).
#   - Run / job ID resolution with attempt probing.
#   - Retry triggering and waiting on subsequent workflow runs.
#   - Outcome handling branches (success / failure / cancelled / timeout).
#   - Slack message payload and explanation updates.
#
# These behaviors depend heavily on the GitHub Actions runtime, gh CLI, and
# Copilot, and full integration-style coverage would require extensive
# mocking of those external systems. To keep the test harness lightweight
# and reliable in this repository, those behaviors are currently exercised
# indirectly via higher-level workflows rather than as isolated unit tests
# in this shell test file.

# Test 1: Wrapper script exists and accepts args
assert "scripts/retry_on_deterministic.sh exists" test -f "$AT_ROOT/scripts/retry_on_deterministic.sh"
assert "retry_orchestrator.sh exists" test -f "$AT_ROOT/modules/retry/retry_orchestrator.sh"

# Test 2: Root retry_on_deterministic.sh execs the script
content=$(cat "$AT_ROOT/retry_on_deterministic.sh")
assert_eq "root retry_on_deterministic execs scripts" "$(echo "$content" | grep -q exec && echo ok || echo fail)" "ok"

# Test 3: Orchestrator with no slack_message exits early (doesn't crash)
TMP_OUTPUT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/auto-triage-retry-orchestrator-test.XXXXXX")"
trap 'rm -rf "$TMP_OUTPUT_DIR"' EXIT
OUTPUT_DIR="$TMP_OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
rc=0
stderr_out="$(bash "$AT_ROOT/retry_on_deterministic.sh" "N150-test" "wf" 2>&1 >/dev/null)" || rc=$?
assert_eq "orchestrator exits gracefully when no slack_message" "$rc" "0"
echo "$stderr_out" | grep -q "No slack_message.json found" >/dev/null 2>&1
msg_rc=$?
assert_eq "orchestrator logs missing slack_message" "$msg_rc" "0"

test_summary

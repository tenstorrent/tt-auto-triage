#!/bin/bash
#
# Tests for lib/hang_detect.sh
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/lib/hang_detect_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"
# shellcheck source=../../lib/hang_detect.sh
source "$AT_ROOT/lib/hang_detect.sh"

echo "=== lib/hang_detect.sh ==="

D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT
mkdir -p "$D/hang_triage"

assert_fails "no markers and no triage files" should_run_hang_followup_analysis "$D"

echo "[HANG DETECTED]" >"$D/error_message.txt"
assert "marker in error_message.txt" should_run_hang_followup_analysis "$D"

rm -f "$D/error_message.txt"
printf 'Card hang detected\n' >"$D/error_message.txt"
assert "Card hang detected marker" should_run_hang_followup_analysis "$D"

rm -f "$D/error_message.txt"
touch "$D/hang_triage/triage_output.txt"
assert "triage_output.txt present" should_run_hang_followup_analysis "$D"

rm -f "$D/hang_triage/triage_output.txt"
echo '{}' >"$D/hang_triage/debug_bus_signal_groups.json"
assert "debug_bus_signal_groups.json present" should_run_hang_followup_analysis "$D"

trap - EXIT
rm -rf "$D"

test_summary

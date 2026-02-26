#!/bin/bash
#
# Tests for modules/analysis/llm_runner.sh
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/analysis/llm_runner_test.sh
#
# Note: Does not invoke real Copilot CLI (would require API calls).
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

_d="$(cd "$SCRIPT_DIR" && pwd)"
while [ "$_d" != "/" ]; do [ -f "$_d/testing_lib_files/test_harness.sh" ] && . "$_d/testing_lib_files/test_harness.sh" && break; _d="${_d%/*}"; done
export AUTO_TRIAGE_ROOT="$ROOT_DIR"

# shellcheck source=../../../modules/analysis/llm_runner.sh
source "$ROOT_DIR/modules/analysis/llm_runner.sh"

echo "=== modules/analysis/llm_runner.sh ==="

# -- run_llm_analysis: missing file -------------------------------------------
set +e
run_llm_analysis "/nonexistent/instructions.txt" "wf" "job" 2>/dev/null
rc=$?
set -e
assert "missing instructions file returns non-zero" [ "$rc" -ne 0 ]

# -- run_llm_analysis: run_llm_analysis is defined -----------------------------
assert "run_llm_analysis is defined" type run_llm_analysis

# -- run_llm_analysis: fails when copilot missing ---------------------------------
# (Skips if copilot is installed - would invoke real API)
tmp_inst=$(mktemp)
echo "test instructions" > "$tmp_inst"
trap 'rm -f "$tmp_inst"' EXIT

if ! command -v copilot >/dev/null 2>&1; then
    set +e
    (run_llm_analysis "$tmp_inst" "wf" "job" 2>/dev/null)
    rc=$?
    set -e
    assert "without copilot, run_llm_analysis fails" [ "$rc" -ne 0 ]
else
    echo "  SKIP  copilot present, skipping invocation test (would call real API)"
fi

test_summary

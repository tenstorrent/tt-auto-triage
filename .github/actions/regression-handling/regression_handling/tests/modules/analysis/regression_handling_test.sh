#!/bin/bash
#
# Tests for regression_handling.sh
#
# Run: cd .github/actions/regression-handling/regression_handling && ./tests/modules/analysis/regression_handling_test.sh
#
# Note: Integration test (full triage run) skipped - requires boundary artifacts and Copilot.
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-handling/regression_handling"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
REGRESSION_HANDLING="$AT_ROOT/regression_handling.sh"

echo "=== regression_handling.sh ==="

# -- missing args -------------------------------------------------------------
set +e
out=$(bash "$REGRESSION_HANDLING" 2>&1)
rc=$?
set -e
assert "missing args exits non-zero" [ "$rc" -ne 0 ]
assert "missing args prints usage" grep -q "Usage" <<< "$out"

# -- one arg (still missing subjob) --------------------------------------------
set +e
out=$(bash "$REGRESSION_HANDLING" "workflow" 2>&1)
rc=$?
set -e
assert "one arg exits non-zero" [ "$rc" -ne 0 ]

# -- boundary artifact validation (jq logic) -----------------------------------
# subjob_runs can be array or {runs: [...]}
tmp_json=$(mktemp)
trap 'rm -f "$tmp_json"' EXIT
echo '[{"status":"success"},{"status":"failure"}]' > "$tmp_json"
fail_count=$(jq 'if type=="array" then ([.[] | select(.status != "success")] | length) else ((.runs // []) | map(select(.status != "success")) | length) end' "$tmp_json")
assert "jq counts failures in array" [ "$fail_count" -eq 1 ]

echo '{"runs":[{"status":"success"},{"status":"failure"},{"status":"failure"}]}' > "$tmp_json"
fail_count=$(jq 'if type=="array" then ([.[] | select(.status != "success")] | length) else ((.runs // []) | map(select(.status != "success")) | length) end' "$tmp_json")
assert "jq counts failures in runs object" [ "$fail_count" -eq 2 ]

# -- LLM delegation: ensure regression_handling.sh invokes copilot ----------------------
# This is a unit-style test: it stubs a `copilot` executable in PATH, prepares
# minimal data/instructions, runs regression_handling.sh, and asserts the stub ran.

# Create stub copilot that logs its invocations.
COPILOT_STUB_DIR="$(mktemp -d)"
COPILOT_STUB_LOG="$(mktemp)"
export COPILOT_STUB_LOG

cat > "$COPILOT_STUB_DIR/copilot" <<'EOF'
#!/bin/bash
# Stub Copilot executable for tests: just record that it was called.
echo "$0 $*" >> "$COPILOT_STUB_LOG"
EOF
chmod +x "$COPILOT_STUB_DIR/copilot"

# Prepend stub directory to PATH, saving original.
ORIGINAL_PATH="$PATH"
export PATH="$COPILOT_STUB_DIR:$PATH"

# Prepare minimal subjob_runs.json. Use canonical path (regression_handling/data) because
# setup_triage_dirs overwrites ./data with a symlink to regression_handling/data.
DATA_DIR="$AT_ROOT/regression_handling/data"
mkdir -p "$DATA_DIR"
SUBJOB_RUNS_JSON="$DATA_DIR/subjob_runs.json"
SUBJOB_RUNS_BAK=""
if [ -f "$SUBJOB_RUNS_JSON" ]; then
    SUBJOB_RUNS_BAK="${SUBJOB_RUNS_JSON}.bak.$$"
    mv "$SUBJOB_RUNS_JSON" "$SUBJOB_RUNS_BAK"
fi
cat > "$SUBJOB_RUNS_JSON" <<'EOF'
{"runs":[{"status":"failure","name":"test-subjob","conclusion":"failure"}]}
EOF

# regression_handling.sh requires instructions_for_llm.txt (not instructions.md). Back it up
# and use minimal content so the script proceeds to invoke copilot.
INSTRUCTIONS_FILE="$AT_ROOT/instructions/instructions_for_llm.txt"
INSTRUCTIONS_BAK=""
if [ -f "$INSTRUCTIONS_FILE" ]; then
    INSTRUCTIONS_BAK="${INSTRUCTIONS_FILE}.bak.$$"
    mv "$INSTRUCTIONS_FILE" "$INSTRUCTIONS_BAK"
fi
echo "Test instructions for regression_handling LLM analysis." > "$INSTRUCTIONS_FILE"

# Run regression_handling.sh with two arguments so it takes the main path.
set +e
bash "$REGRESSION_HANDLING" "dummy-workflow" "test-subjob" >/dev/null 2>&1
# We don't assert on the exit code here; the goal is to see that copilot ran.
set -e

# Assert that the stub copilot was invoked at least once.
assert "regression_handling delegates to run_llm_analysis (copilot invoked)" [ -s "$COPILOT_STUB_LOG" ]

# Cleanup: restore PATH and any backed-up files, and remove temp artifacts.
export PATH="$ORIGINAL_PATH"
rm -rf "$COPILOT_STUB_DIR"
rm -f "$COPILOT_STUB_LOG"
if [ -n "${SUBJOB_RUNS_BAK:-}" ] && [ -f "$SUBJOB_RUNS_BAK" ]; then
    mv "$SUBJOB_RUNS_BAK" "$SUBJOB_RUNS_JSON"
else
    rm -f "$SUBJOB_RUNS_JSON"
fi
if [ -n "${INSTRUCTIONS_BAK:-}" ] && [ -f "$INSTRUCTIONS_BAK" ]; then
    mv "$INSTRUCTIONS_BAK" "$INSTRUCTIONS_FILE"
else
    rm -f "$INSTRUCTIONS_FILE"
fi
test_summary

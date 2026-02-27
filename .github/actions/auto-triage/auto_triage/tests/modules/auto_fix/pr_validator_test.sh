#!/bin/bash
#
# Tests for modules/auto_fix/pr_validator.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/auto_fix/pr_validator_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
source "$AT_ROOT/modules/auto_fix/pr_validator.sh"

echo "=== modules/auto_fix/pr_validator.sh ==="

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# -- is_auto_fix_enabled -------------------------------------------------------

echo '{"create_PR": true}' > "$TMP_DIR/enabled.json"
assert "enabled when create_PR=true" is_auto_fix_enabled "$TMP_DIR/enabled.json"

echo '{"create_PR": false}' > "$TMP_DIR/disabled.json"
assert_fails "disabled when create_PR=false" is_auto_fix_enabled "$TMP_DIR/disabled.json"

echo '{}' > "$TMP_DIR/empty.json"
assert_fails "disabled when create_PR missing" is_auto_fix_enabled "$TMP_DIR/empty.json"

assert_fails "disabled when file missing (creates default)" is_auto_fix_enabled "$TMP_DIR/nonexistent.json"
assert "default file created" test -f "$TMP_DIR/nonexistent.json"

assert_fails "fails with empty path" is_auto_fix_enabled ""

# -- validate_explanation_file --------------------------------------------------

echo "some explanation content" > "$TMP_DIR/explanation.md"
assert "valid explanation file" validate_explanation_file "$TMP_DIR/explanation.md"

touch "$TMP_DIR/empty_explanation.md"
assert_fails "empty explanation file" validate_explanation_file "$TMP_DIR/empty_explanation.md"

assert_fails "missing explanation file" validate_explanation_file "$TMP_DIR/no_such_file.md"

assert_fails "fails with empty path" validate_explanation_file ""

# -- validate_workspace ---------------------------------------------------------

mkdir -p "$TMP_DIR/valid_ws/.git"
assert "valid workspace with .git" validate_workspace "$TMP_DIR/valid_ws"

mkdir -p "$TMP_DIR/no_git_ws"
assert_fails "workspace without .git" validate_workspace "$TMP_DIR/no_git_ws"

assert_fails "non-existent workspace" validate_workspace "$TMP_DIR/no_such_dir"

assert_fails "fails with empty path" validate_workspace ""

# -- summary -------------------------------------------------------------------
test_summary

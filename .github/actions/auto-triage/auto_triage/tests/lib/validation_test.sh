#!/bin/bash
#
# Smoke tests for lib/validation.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/lib/validation_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/lib"

source "$SCRIPT_DIR/test_harness.sh"
export AUTO_TRIAGE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$LIB_DIR/validation.sh"

echo "=== lib/validation.sh ==="

# -- is_valid_sha_format -------------------------------------------------------
assert "sha format: full 40-char"   is_valid_sha_format "abc123def456abc123def456abc123def456abc1"
assert "sha format: short 7-char"   is_valid_sha_format "abc123d"
assert_fails "sha format: too short (6)" eval 'is_valid_sha_format "abc123"'
assert_fails "sha format: uppercase"     eval 'is_valid_sha_format "ABC123DEF"'
assert_fails "sha format: non-hex"       eval 'is_valid_sha_format "xyz1234"'
assert_fails "sha format: empty"         eval 'is_valid_sha_format ""'

# -- parse_job_url -------------------------------------------------------------
url="https://github.com/tenstorrent/tt-metal/actions/runs/12345678/job/87654321"
assert "parse_job_url valid" parse_job_url "$url"
assert_eq "parse_job_url owner"  "$_owner"  "tenstorrent"
assert_eq "parse_job_url repo"   "$_repo"   "tt-metal"
assert_eq "parse_job_url run_id" "$_run_id" "12345678"
assert_eq "parse_job_url job_id" "$_job_id" "87654321"

assert_fails "parse_job_url invalid" parse_job_url "https://example.com/not/a/job"
assert_fails "parse_job_url empty"   parse_job_url ""

# -- validate_json_file --------------------------------------------------------
TMP=$(mktemp)

echo '{"ok":true}' > "$TMP"
assert "validate_json_file valid" validate_json_file "$TMP"

assert_fails "validate_json_file missing" validate_json_file "/no/such/file.json" "quiet"

: > "$TMP"   # empty the file
assert_fails "validate_json_file empty" validate_json_file "$TMP" "quiet"

echo "not json" > "$TMP"
assert_fails "validate_json_file invalid content" validate_json_file "$TMP" "quiet"

rm -f "$TMP"

# -- ensure_dirs ---------------------------------------------------------------
TMP_DIR=$(mktemp -d)
ensure_dirs "$TMP_DIR/a/b" "$TMP_DIR/c"
assert "ensure_dirs creates nested" test -d "$TMP_DIR/a/b"
assert "ensure_dirs creates flat"   test -d "$TMP_DIR/c"
rm -rf "$TMP_DIR"

# -- double-source guard -------------------------------------------------------
(
    source "$LIB_DIR/validation.sh"
    true
) && { echo "  PASS  double-source guard"; _pass=$((_pass + 1)); } \
  || { echo "  FAIL  double-source guard"; _fail=$((_fail + 1)); }

test_summary

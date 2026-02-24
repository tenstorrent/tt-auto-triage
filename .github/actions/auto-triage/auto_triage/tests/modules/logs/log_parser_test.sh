#!/bin/bash
#
# Tests for modules/logs/log_parser.sh
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/logs/log_parser_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

source "$SCRIPT_DIR/../../lib/test_harness.sh"
export AUTO_TRIAGE_ROOT="$ROOT_DIR"

# shellcheck source=../../../modules/logs/log_parser.sh
source "$ROOT_DIR/modules/logs/log_parser.sh"

echo "=== modules/logs/log_parser.sh ==="

# -- sanitize_job_name --------------------------------------------------------
assert_eq "sanitize lowercase" "$(sanitize_job_name "myjob")" "myjob"
assert_eq "sanitize strips non-alnum" "$(sanitize_job_name "My-Job_Name")" "myjobname"
assert_eq "sanitize empty" "$(sanitize_job_name "")" ""

# -- parse_job_url (via validation) -------------------------------------------
url="https://github.com/tenstorrent/tt-metal/actions/runs/12345678/job/87654321"
assert "parse_job_url valid" parse_job_url "$url"
assert_eq "parse_job_url owner" "$_owner" "tenstorrent"
assert_eq "parse_job_url repo" "$_repo" "tt-metal"
assert_eq "parse_job_url run_id" "$_run_id" "12345678"
assert_eq "parse_job_url job_id" "$_job_id" "87654321"

assert_fails "parse_job_url invalid" parse_job_url "https://example.com/not/a/job"

# -- find_job_logs ------------------------------------------------------------
TMP_LOGS=$(mktemp -d)
trap 'rm -rf "$TMP_LOGS"' EXIT
mkdir -p "$TMP_LOGS/my-job-name/nested"
mkdir -p "$TMP_LOGS/other-job"
echo "x" > "$TMP_LOGS/my-job-name/log.txt"
echo "y" > "$TMP_LOGS/other-job/log.txt"
echo "z" > "$TMP_LOGS/my-job-name/nested/out.log"

found=$(find_job_logs "$TMP_LOGS" "my-job-name")
count=$(echo "$found" | grep -c . 2>/dev/null || true)
assert "find_job_logs finds matching files" [ "$count" -ge 2 ]

test_summary

#!/bin/bash
#
# Tests for scripts/build_slack_payload.sh and scripts/slack_message.jq
#
# Run: cd .github/actions/slack-report-auto-triage && bash tests/slack_report/payload_builder_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/slack-report-auto-triage"
TEST_DIR="$AT_ROOT/tests/slack_report"
SCRIPTS_DIR="$AT_ROOT/scripts"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"

BUILD_SCRIPT="$SCRIPTS_DIR/build_slack_payload.sh"
SLACK_MESSAGE_JQ="$SCRIPTS_DIR/slack_message.jq"
SAMPLE_MSG="$TEST_DIR/sample_slack_message.json"
SAMPLE_CASE4_MSG="$TEST_DIR/sample_case4_slack_message.json"

echo "=== payload_builder ==="

# -- Scripts exist ------------------------------------------------------------
assert "build_slack_payload.sh exists" [ -f "$BUILD_SCRIPT" ]
assert "slack_message.jq exists" [ -f "$SLACK_MESSAGE_JQ" ]
assert "sample_slack_message.json exists" [ -f "$SAMPLE_MSG" ]
assert "sample_case4_slack_message.json exists" [ -f "$SAMPLE_CASE4_MSG" ]

# -- Cancellation: with thread_ts, failing_run, error_msg ----------------------
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
export GITHUB_OUTPUT="$tmpdir/github_output"
export GITHUB_REPOSITORY="owner/repo"
export GITHUB_RUN_ID="999"
export GITHUB_RUN_NUMBER="42"
export JOB_NAME="my-job"
export WORKFLOW_NAME="my-workflow"
export SLACK_TS="1234567890.123456"
export ALLOW_PINGS="false"

mkdir -p "$tmpdir/.auto_triage/data"
echo '{"should_cancel": true, "message": "Hardware check failed"}' > "$tmpdir/.auto_triage/cancel.json"
echo "Error details here" > "$tmpdir/.auto_triage/data/error_message.txt"
echo '[{"status":"failure","run_number":5,"job_url":"https://run.url","run_id":"555"}]' > "$tmpdir/.auto_triage/data/subjob_runs.json"

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

payload=$(cat "$tmpdir/.auto_triage/slack_payload.json")
assert "Cancellation payload has text" [ -n "$(echo "$payload" | jq -r '.text // empty')" ]
assert "Cancellation payload has thread_ts" [ "$(echo "$payload" | jq -r '.thread_ts')" = "1234567890.123456" ]
assert "Cancellation text contains FAILING RUN" [ -n "$(echo "$payload" | jq -r '.text' | grep -F 'FAILING RUN' || true)" ]
assert "Cancellation text contains FAILURE MESSAGE" [ -n "$(echo "$payload" | jq -r '.text' | grep -F 'FAILURE MESSAGE' || true)" ]
assert "Cancellation text contains error details" [ -n "$(echo "$payload" | jq -r '.text' | grep -F 'Error details here' || true)" ]
assert "has_payload=true" grep -q "has_payload=true" "$tmpdir/github_output"
assert "payload_file set" grep -q "payload_file=" "$tmpdir/github_output"

# -- Cancellation: without thread_ts, failing_run, error_msg -------------------
rm -f "$tmpdir/github_output" "$tmpdir/.auto_triage/slack_payload.json"
rm -f "$tmpdir/.auto_triage/data/error_message.txt" "$tmpdir/.auto_triage/data/subjob_runs.json"
echo '{"should_cancel": true, "message": "Cancelled"}' > "$tmpdir/.auto_triage/cancel.json"
unset SLACK_TS

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

payload2=$(cat "$tmpdir/.auto_triage/slack_payload.json")
assert "Cancellation without thread_ts has no thread_ts" [ "$(echo "$payload2" | jq -r '.thread_ts // empty')" = "" ]
assert "Cancellation text contains Workflow" [ -n "$(echo "$payload2" | jq -r '.text' | grep -F 'Workflow' || true)" ]
assert "Cancellation text has no FAILING RUN section" [ -z "$(echo "$payload2" | jq -r '.text' | grep -F 'FAILING RUN' || true)" ]

# -- Normal report: from sample slack_message.json -----------------------------
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
rm -f "$tmpdir/github_output"

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

payload3=$(cat "$tmpdir/.auto_triage/slack_payload.json")
text3=$(echo "$payload3" | jq -r '.text // empty')
assert "Normal report has text" [ -n "$text3" ]
assert "Normal report contains FAILING TEST" [ -n "$(echo "$text3" | grep -F 'test_matmul' || true)" ]
assert "Normal report contains RELEVANT DEVELOPERS" [ -n "$(echo "$text3" | grep -F 'RELEVANT DEVELOPERS' || true)" ]
assert "Normal report has no thread_ts" [ "$(echo "$payload3" | jq -r '.thread_ts // empty')" = "" ]
assert "has_payload=true for normal" grep -q "has_payload=true" "$tmpdir/github_output"

# -- Case 4 report: compact formatting and ping behavior -----------------------
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_CASE4_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
export ALLOW_PINGS="true"
rm -f "$tmpdir/github_output"

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

payload4=$(cat "$tmpdir/.auto_triage/slack_payload.json")
text4=$(echo "$payload4" | jq -r '.text // empty')
assert "Case 4 report has text" [ -n "$text4" ]
assert "Case 4 contains failing test" [ -n "$(echo "$text4" | grep -F 'test_multi_chip_plan' || true)" ]
assert "Case 4 includes compact caveat line" [ -n "$(echo "$text4" | grep -F 'Could not identify a single high-confidence culprit commit.' || true)" ]
assert "Case 4 includes summary text" [ -n "$(echo "$text4" | grep -F 'no single culprit stands out' || true)" ]
assert "Case 4 omits verbose commits section" [ -z "$(echo "$text4" | grep -F '*COMMITS:*' || true)" ]
assert "Case 4 omits per-commit hash lines" [ -z "$(echo "$text4" | grep -F 'HASH:' || true)" ]
assert "Case 4 omits approvers block" [ -z "$(echo "$text4" | grep -F 'APPROVERS:' || true)" ]
assert "Case 4 omits confidence lines" [ -z "$(echo "$text4" | grep -F 'CONFIDENCE:' || true)" ]
assert "Case 4 pings top-level relevant developers only" [ -n "$(echo "$text4" | grep -F '<@U07G7EXAMPLE>' || true)" ]
assert "Case 4 does not ping commit author" [ -z "$(echo "$text4" | grep -F '<@U04D4EXAMPLE>' || true)" ]
assert "Case 4 does not ping approver subteam" [ -z "$(echo "$text4" | grep -F '<!subteam^S06F6EXAMPLE' || true)" ]
assert "Case 4 has no thread_ts" [ "$(echo "$payload4" | jq -r '.thread_ts // empty')" = "" ]

# -- Empty/missing message path: has_payload=false ------------------------------
export MESSAGE_PATH="$tmpdir/nonexistent.json"
rm -f "$tmpdir/github_output" "$tmpdir/.auto_triage/slack_payload.json"

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

assert "has_payload=false when file missing" grep -q "has_payload=false" "$tmpdir/github_output"

export MESSAGE_PATH=""
rm -f "$tmpdir/github_output"
cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null
assert "has_payload=false when MESSAGE_PATH empty" grep -q "has_payload=false" "$tmpdir/github_output"

test_summary

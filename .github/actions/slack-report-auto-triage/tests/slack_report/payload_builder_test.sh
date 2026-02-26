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

echo "=== payload_builder ==="

# -- Scripts exist ------------------------------------------------------------
assert "build_slack_payload.sh exists" [ -f "$BUILD_SCRIPT" ]
assert "slack_message.jq exists" [ -f "$SLACK_MESSAGE_JQ" ]
assert "sample_slack_message.json exists" [ -f "$SAMPLE_MSG" ]

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

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
SAMPLE_CASE5_MSG="$TEST_DIR/sample_case5_slack_message.json"

echo "=== payload_builder ==="

# -- Scripts exist ------------------------------------------------------------
assert "build_slack_payload.sh exists" [ -f "$BUILD_SCRIPT" ]
assert "slack_message.jq exists" [ -f "$SLACK_MESSAGE_JQ" ]
assert "sample_slack_message.json exists" [ -f "$SAMPLE_MSG" ]
assert "sample_case4_slack_message.json exists" [ -f "$SAMPLE_CASE4_MSG" ]
assert "sample_case5_slack_message.json exists" [ -f "$SAMPLE_CASE5_MSG" ]

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
# Group resolution is intentionally skipped here (no slack_groups.json / slack_directory.json fixtures),
# so the S-prefixed ID in the top-level relevant_developers stays unresolved and renders as plain text.
assert "Case 4 skips group resolution: no slack_groups.json fixture" [ ! -f "$tmpdir/.auto_triage/data/slack_groups.json" ]
assert "Case 4 skips group resolution: no slack_directory.json fixture" [ ! -f "$tmpdir/.auto_triage/data/slack_directory.json" ]
assert "Case 4 unresolved S-prefixed top-level developer renders as plain text name" [ -n "$(echo "$text4" | grep -F 'Graph Runtime Owners' || true)" ]
assert "Case 4 unresolved S-prefixed top-level developer not rendered as subteam ping" [ -z "$(echo "$text4" | grep -F '<!subteam^S10K0EXAMPLE' || true)" ]
assert "Case 4 has no thread_ts" [ "$(echo "$payload4" | jq -r '.thread_ts // empty')" = "" ]

# -- Case 5 report: commit truncation (only top commit shown) -----------------
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_CASE5_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
export ALLOW_PINGS="true"
rm -f "$tmpdir/github_output"

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

payload5=$(cat "$tmpdir/.auto_triage/slack_payload.json")
text5=$(echo "$payload5" | jq -r '.text // empty')
assert "Case 5 report has text" [ -n "$text5" ]
assert "Case 5 contains COMMITS section" [ -n "$(echo "$text5" | grep -F '*COMMITS:*' || true)" ]
assert "Case 5 shows top commit hash" [ -n "$(echo "$text5" | grep -F '25eb02e7' || true)" ]
assert "Case 5 does NOT show second commit hash" [ -z "$(echo "$text5" | grep -F 'b79cfdc4' || true)" ]
assert "Case 5 does NOT show third commit hash" [ -z "$(echo "$text5" | grep -F '71649328' || true)" ]
assert "Case 5 shows truncation note" [ -n "$(echo "$text5" | grep -F 'more commit(s) in full report' || true)" ]
assert "Case 5 truncation note says 2 more" [ -n "$(echo "$text5" | grep -F '2 more commit(s)' || true)" ]
assert "Case 5 pings top-level relevant developers" [ -n "$(echo "$text5" | grep -F 'RELEVANT DEVELOPERS' || true)" ]

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

# -- JOB_OWNER_FILE with mixed U/S IDs: user pinged, group stays plain text ----
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_CASE4_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
export ALLOW_PINGS="true"
rm -f "$tmpdir/github_output"

# Write a job_owner.json with one U-prefixed user and one S-prefixed group
cat > "$tmpdir/.auto_triage/data/job_owner.json" <<'EOF'
[
  {"name": "Alice Dev", "slack_id": "U99USEREX"},
  {"name": "Core Team", "slack_id": "S88GROUPEX"}
]
EOF

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

payload_owner=$(cat "$tmpdir/.auto_triage/slack_payload.json")
text_owner=$(echo "$payload_owner" | jq -r '.text // empty')
assert "JOB OWNER section present" [ -n "$(echo "$text_owner" | grep -F 'JOB OWNER' || true)" ]
assert "JOB OWNER U-prefixed user is pinged" [ -n "$(echo "$text_owner" | grep -F '<@U99USEREX>' || true)" ]
assert "JOB OWNER S-prefixed group is not pinged as subteam" [ -z "$(echo "$text_owner" | grep -F '<!subteam^S88GROUPEX' || true)" ]
assert "JOB OWNER S-prefixed group renders as plain text name" [ -n "$(echo "$text_owner" | grep -F 'Core Team' || true)" ]


# -- commits_section edge cases: 1 commit (no truncation note) ----------------
# Test that a single commit does NOT produce a "more commit(s)" note.
single_commit_json=$(jq -n '{
  "case": 1,
  "commits": [{"hash": "aabbccdd", "url": "", "author": {"name": "Dev", "slack_id": "U_DEV"}, "confidence": 100}],
  "relevant_developers": [],
  "failing_test_name": "test_foo",
  "scenario": "test"
}')

text_single=$(echo "$single_commit_json" | jq -r -f "$SCRIPTS_DIR/slack_message.jq" \
  --arg run_url "http://example.com/1" \
  --arg run_label "Run #1" \
  --arg job_name "my-job" \
  --arg workflow_name "my-workflow" \
  --arg auto_fix "" \
  --arg allow_pings "false" \
  --argjson job_owner '[]')
assert "1-commit: COMMITS section present" [ -n "$(echo "$text_single" | grep -F '*COMMITS:*' || true)" ]
assert "1-commit: no truncation note" [ -z "$(echo "$text_single" | grep -F 'more commit(s)' || true)" ]

# -- commits_section edge cases: 2 commits (shows "1 more") -------------------
two_commit_json=$(jq -n '{
  "case": 1,
  "commits": [
    {"hash": "aaaa1111", "url": "", "author": {"name": "Dev A", "slack_id": "U_A"}, "confidence": 100},
    {"hash": "bbbb2222", "url": "", "author": {"name": "Dev B", "slack_id": "U_B"}, "confidence": 80}
  ],
  "relevant_developers": [],
  "failing_test_name": "test_bar",
  "scenario": "test"
}')

text_two=$(echo "$two_commit_json" | jq -r -f "$SCRIPTS_DIR/slack_message.jq" \
  --arg run_url "http://example.com/2" \
  --arg run_label "Run #2" \
  --arg job_name "my-job" \
  --arg workflow_name "my-workflow" \
  --arg auto_fix "" \
  --arg allow_pings "false" \
  --argjson job_owner '[]')
assert "2-commits: shows first hash" [ -n "$(echo "$text_two" | grep -F 'aaaa1111' || true)" ]
assert "2-commits: does not show second hash" [ -z "$(echo "$text_two" | grep -F 'bbbb2222' || true)" ]
assert "2-commits: truncation note says 1 more" [ -n "$(echo "$text_two" | grep -F '1 more commit(s)' || true)" ]

# -- JOB_OWNER representative suffix is preserved with pings --------------------
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_CASE4_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
export ALLOW_PINGS="true"
rm -f "$tmpdir/github_output"

cat > "$tmpdir/.auto_triage/data/job_owner.json" <<'EOF'
[
  {"name": "Rose Li (representing Metal Infra Team)", "slack_id": "U08DEGUJY3H"}
]
EOF

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

payload_owner_rep=$(cat "$tmpdir/.auto_triage/slack_payload.json")
text_owner_rep=$(echo "$payload_owner_rep" | jq -r '.text // empty')
assert "JOB OWNER representative owner is pinged" [ -n "$(echo "$text_owner_rep" | grep -F '<@U08DEGUJY3H>' || true)" ]
assert "JOB OWNER representative suffix is preserved" [ -n "$(echo "$text_owner_rep" | grep -F '(representing Metal Infra Team)' || true)" ]

# JOB OWNER must be visually separated from the previous section by a blank line.
# Inspect the line directly above "*JOB OWNER:*" -- it must be empty. We use awk
# instead of grep -Pz so the assertion works on both macOS BSD grep and GNU grep.
line_above_owner=$(printf '%s\n' "$text_owner_rep" | awk '/^\*JOB OWNER:\*/{print prev; exit} {prev=$0}')
assert_eq "JOB OWNER section is preceded by a blank line" "$line_above_owner" ""

# -- JOB_OWNER ID-only entries are kept (not silently dropped) ----------------
# Emulates the case where fetch_job_owner.py captured a <@U...> mention but the
# Slack directory cache didn't contain the user, so the entry has slack_id but
# no name. build_slack_payload.sh must not filter it out.
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_CASE4_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
export ALLOW_PINGS="true"
rm -f "$tmpdir/github_output"

cat > "$tmpdir/.auto_triage/data/job_owner.json" <<'EOF'
[
  {"name": "", "slack_id": "U999IDONLY"}
]
EOF

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

text_id_only=$(jq -r '.text // empty' "$tmpdir/.auto_triage/slack_payload.json")
assert "ID-only JOB OWNER entry is preserved (not dropped)" \
  [ -n "$(echo "$text_id_only" | grep -F '*JOB OWNER:*' || true)" ]
assert "ID-only JOB OWNER entry renders as ping" \
  [ -n "$(echo "$text_id_only" | grep -F '<@U999IDONLY>' || true)" ]

# -- JOB_OWNER default-owner disclaimer for metalinfra fallback ---------------
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_CASE4_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
export ALLOW_PINGS="true"
rm -f "$tmpdir/github_output"

cat > "$tmpdir/.auto_triage/data/job_owner.json" <<'EOF'
[
  {"name": "Rose Li (representing Metal Infra Team)", "slack_id": "U08DEGUJY3H", "is_default_owner": true}
]
EOF

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

text_default_owner=$(jq -r '.text // empty' "$tmpdir/.auto_triage/slack_payload.json")
assert "Default-owner metalinfra is pinged" \
  [ -n "$(echo "$text_default_owner" | grep -F '<@U08DEGUJY3H>' || true)" ]
assert "Default-owner disclaimer is appended" \
  [ -n "$(echo "$text_default_owner" | grep -F 'Metalinfra was chosen as the default owner' || true)" ]
assert "Default-owner disclaimer includes action request" \
  [ -n "$(echo "$text_default_owner" | grep -F 'Please find a suitable owner' || true)" ]

# -- JOB_OWNER explicit metalinfra (no disclaimer) ----------------------------
rm -rf "$tmpdir/.auto_triage"
mkdir -p "$tmpdir/.auto_triage/output" "$tmpdir/.auto_triage/data"
cp "$SAMPLE_CASE4_MSG" "$tmpdir/.auto_triage/output/slack_message.json"
export MESSAGE_PATH="$tmpdir/.auto_triage/output/slack_message.json"
export SLACK_TS=""
export ALLOW_PINGS="true"
rm -f "$tmpdir/github_output"

# Simulate metalinfra as explicit owner (no is_default_owner flag)
cat > "$tmpdir/.auto_triage/data/job_owner.json" <<'EOF'
[
  {"name": "Rose Li (representing Metal Infra Team)", "slack_id": "U08DEGUJY3H"}
]
EOF

cd "$tmpdir"
bash "$BUILD_SCRIPT"
cd - >/dev/null

text_explicit_owner=$(jq -r '.text // empty' "$tmpdir/.auto_triage/slack_payload.json")
assert "Explicit metalinfra owner is pinged" \
  [ -n "$(echo "$text_explicit_owner" | grep -F '<@U08DEGUJY3H>' || true)" ]
assert "Explicit metalinfra has no default-owner disclaimer" \
  [ -z "$(echo "$text_explicit_owner" | grep -F 'Metalinfra was chosen as the default owner' || true)" ]

test_summary

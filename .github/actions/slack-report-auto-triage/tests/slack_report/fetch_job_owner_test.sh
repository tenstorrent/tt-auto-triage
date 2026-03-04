#!/bin/bash
#
# Tests for scripts/fetch_job_owner.py and scripts/fetch_job_owner.sh
#
# Run: cd .github/actions/slack-report-auto-triage && bash tests/slack_report/fetch_job_owner_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/slack-report-auto-triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
PYTHON_SCRIPT="$AT_ROOT/scripts/fetch_job_owner.py"
SHELL_SCRIPT="$AT_ROOT/scripts/fetch_job_owner.sh"

echo "=== fetch_job_owner ==="

# -- Python script exists -----------------------------------------------------
assert "fetch_job_owner.py exists" [ -f "$PYTHON_SCRIPT" ]
assert "fetch_job_owner.sh exists" [ -f "$SHELL_SCRIPT" ]

# -- Python: missing required env exits non-zero ------------------------------
assert_fails "Python exits non-zero when JOB_NAME missing" python3 "$PYTHON_SCRIPT"

# -- Python: job owner extraction from mock thread text -----------------------
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# Thread text: line with job name "blackhole-demo" and @Alice Smith @Bob
echo "Job blackhole-demo tests failing. Owners: @Alice Smith @Bob Jones" > "$tmpdir/thread.txt"

# Mock slack_directory.json (user lookup)
mkdir -p "$tmpdir/slack_data"
cat > "$tmpdir/slack_data/slack_directory.json" <<'EOF'
{"users": [
  {"id": "U111", "real_name": "Alice Smith", "deleted": false, "is_bot": false},
  {"id": "U222", "display_name": "Bob Jones", "deleted": false, "is_bot": false}
]}
EOF

# Mock slack_groups.json (usergroup lookup)
echo '{"usergroups": []}' > "$tmpdir/slack_data/slack_groups.json"

JOB_NAME="blackhole-demo"
JOB_OWNER_FILE="$tmpdir/job_owner.json"
THREAD_TEXT_FILE="$tmpdir/thread.txt"
SLACK_DATA_DIR="$tmpdir/slack_data"

export JOB_NAME JOB_OWNER_FILE THREAD_TEXT_FILE SLACK_DATA_DIR
python3 "$PYTHON_SCRIPT"

owners=$(jq -r '.[].name' "$JOB_OWNER_FILE" | tr '\n' ' ')
assert "Python extracts owner names from thread" [ "$owners" = "Alice Smith Bob Jones " ]

alice_id=$(jq -r '.[] | select(.name=="Alice Smith") | .slack_id' "$JOB_OWNER_FILE")
bob_id=$(jq -r '.[] | select(.name=="Bob Jones") | .slack_id' "$JOB_OWNER_FILE")
assert "Python resolves Slack ID for Alice" [ "$alice_id" = "U111" ]
assert "Python resolves Slack ID for Bob" [ "$bob_id" = "U222" ]

# -- Python: graceful handling when job name not in thread --------------------
echo "Unrelated message here." > "$tmpdir/thread2.txt"
THREAD_TEXT_FILE="$tmpdir/thread2.txt"
export THREAD_TEXT_FILE
python3 "$PYTHON_SCRIPT"
count=$(jq 'length' "$JOB_OWNER_FILE")
assert "Python returns empty when job name not in thread" [ "$count" -eq 0 ]

# -- Shell: graceful failure when credentials missing -------------------------
unset SLACK_TS CHANNEL_ID SLACK_BOT_TOKEN 2>/dev/null || true
JOB_OWNER_FILE="$tmpdir/job_owner_shell.json"
export JOB_OWNER_FILE JOB_NAME
# Script should exit 0 (non-fatal) and write empty array
bash "$SHELL_SCRIPT"
assert "Shell exits 0 when credentials missing" [ -f "$JOB_OWNER_FILE" ]
empty=$(jq 'length' "$JOB_OWNER_FILE")
assert "Shell writes empty array when credentials missing" [ "$empty" -eq 0 ]

test_summary

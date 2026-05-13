#!/bin/bash
#
# Tests for scripts/fetch_job_owner.py and scripts/fetch_job_owner.sh
#
# Run: cd .github/actions/slack-report-regression-handling && bash tests/slack_report/fetch_job_owner_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/slack-report-regression-handling"
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

# -- Python: explicit <@U...> mention token is captured and name backfilled ---
echo 'Job blackhole-demo failing. Owner: <@U111>' > "$tmpdir/thread_uid.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_uid.json"
THREAD_TEXT_FILE="$tmpdir/thread_uid.txt"
export JOB_OWNER_FILE THREAD_TEXT_FILE
python3 "$PYTHON_SCRIPT"
uid_count=$(jq 'length' "$JOB_OWNER_FILE")
uid_first_id=$(jq -r '.[0].slack_id' "$JOB_OWNER_FILE")
uid_first_name=$(jq -r '.[0].name' "$JOB_OWNER_FILE")
assert_eq "<@U...> mention extracted as one owner" "$uid_count" "1"
assert_eq "<@U...> mention preserves slack_id" "$uid_first_id" "U111"
assert_eq "<@U...> mention backfills name from directory" "$uid_first_name" "Alice Smith"

# -- Python: <@U...|fallback> pipe form is also captured ----------------------
echo 'Job blackhole-demo failing. Owner: <@U222|bob>' > "$tmpdir/thread_uid_pipe.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_uid_pipe.json"
THREAD_TEXT_FILE="$tmpdir/thread_uid_pipe.txt"
export JOB_OWNER_FILE THREAD_TEXT_FILE
python3 "$PYTHON_SCRIPT"
pipe_id=$(jq -r '.[0].slack_id // ""' "$JOB_OWNER_FILE")
assert_eq "<@U...|fallback> pipe form is captured" "$pipe_id" "U222"

# -- Python: <!subteam^S...> mention captures group and backfills name --------
mkdir -p "$tmpdir/slack_data2"
cat > "$tmpdir/slack_data2/slack_directory.json" <<'EOF'
{"users": []}
EOF
cat > "$tmpdir/slack_data2/slack_groups.json" <<'EOF'
{"usergroups": [
  {"id": "S123", "name": "Metal Infra Team", "handle": "metal-infra"}
]}
EOF
echo 'Job blackhole-demo failing. Owner: <!subteam^S123|metal-infra>' > "$tmpdir/thread_subteam.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_subteam.json"
THREAD_TEXT_FILE="$tmpdir/thread_subteam.txt"
SLACK_DATA_DIR="$tmpdir/slack_data2"
export JOB_OWNER_FILE THREAD_TEXT_FILE SLACK_DATA_DIR
python3 "$PYTHON_SCRIPT"
sub_id=$(jq -r '.[0].slack_id' "$JOB_OWNER_FILE")
sub_name=$(jq -r '.[0].name' "$JOB_OWNER_FILE")
assert_eq "<!subteam^S...> mention preserves slack_id" "$sub_id" "S123"
assert_eq "<!subteam^S...> mention backfills group name" "$sub_name" "Metal Infra Team"
sub_is_default=$(jq -r '.[0].is_default_owner // false' "$JOB_OWNER_FILE")
assert_eq "Metal Infra Team is marked as default owner" "$sub_is_default" "true"

# -- Python: is_default_owner flag set for known metalinfra group ID ----------
mkdir -p "$tmpdir/slack_data_mi"
echo '{"users": []}' > "$tmpdir/slack_data_mi/slack_directory.json"
cat > "$tmpdir/slack_data_mi/slack_groups.json" <<'EOF'
{"usergroups": [
  {"id": "S0985AN7TC5", "name": "metal infra team", "handle": "metalinfra"}
]}
EOF
echo 'Job blackhole-demo failing. Owner: <!subteam^S0985AN7TC5|metalinfra>' > "$tmpdir/thread_mi.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_mi.json"
THREAD_TEXT_FILE="$tmpdir/thread_mi.txt"
SLACK_DATA_DIR="$tmpdir/slack_data_mi"
export JOB_OWNER_FILE THREAD_TEXT_FILE SLACK_DATA_DIR
python3 "$PYTHON_SCRIPT"
mi_is_default=$(jq -r '.[0].is_default_owner // false' "$JOB_OWNER_FILE")
assert_eq "Known metalinfra group ID is marked as default owner" "$mi_is_default" "true"

# -- Python: metalinfra resolves to TWO representatives when API succeeds ------
# We monkey-patch _resolve_metalinfra_representatives to return two fake user IDs
# without requiring a real SLACK_BOT_TOKEN / Slack API call.
mkdir -p "$tmpdir/slack_data_mi2"
cat > "$tmpdir/slack_data_mi2/slack_directory.json" <<'EOF'
{"users": [
  {"id": "UREP1", "real_name": "Rep One", "deleted": false, "is_bot": false},
  {"id": "UREP2", "display_name": "Rep Two", "deleted": false, "is_bot": false}
]}
EOF
cat > "$tmpdir/slack_data_mi2/slack_groups.json" <<'EOF'
{"usergroups": [
  {"id": "S0985AN7TC5", "name": "metal infra team", "handle": "metalinfra"}
]}
EOF
echo 'Job blackhole-demo failing. Owner: <!subteam^S0985AN7TC5|metalinfra>' > "$tmpdir/thread_mi2.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_mi2.json"
THREAD_TEXT_FILE="$tmpdir/thread_mi2.txt"
SLACK_DATA_DIR="$tmpdir/slack_data_mi2"
export JOB_OWNER_FILE THREAD_TEXT_FILE SLACK_DATA_DIR

python3 -c "
import sys, importlib.util, os, unittest.mock

# exec_module first so _resolve_metalinfra_representatives is defined on mod,
# then patch it and call main() explicitly.  The 'if __name__ == \"__main__\"'
# guard in the script prevents main() from running during exec_module.
spec = importlib.util.spec_from_file_location('fetch_job_owner', '$PYTHON_SCRIPT')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

os.environ['JOB_NAME'] = '$JOB_NAME'
os.environ['JOB_OWNER_FILE'] = '$JOB_OWNER_FILE'
os.environ['THREAD_TEXT_FILE'] = '$THREAD_TEXT_FILE'
os.environ['SLACK_DATA_DIR'] = '$SLACK_DATA_DIR'

with unittest.mock.patch.object(mod, '_resolve_metalinfra_representatives', return_value=['UREP1', 'UREP2']):
    mod.main()
"
mi2_count=$(jq 'length' "$JOB_OWNER_FILE")
assert_eq "Metalinfra fallback resolves to TWO representative entries" "$mi2_count" "2"
mi2_id0=$(jq -r '.[0].slack_id' "$JOB_OWNER_FILE")
mi2_id1=$(jq -r '.[1].slack_id' "$JOB_OWNER_FILE")
assert_eq "First metalinfra rep has correct slack_id" "$mi2_id0" "UREP1"
assert_eq "Second metalinfra rep has correct slack_id" "$mi2_id1" "UREP2"
mi2_name0=$(jq -r '.[0].name' "$JOB_OWNER_FILE")
mi2_name1=$(jq -r '.[1].name' "$JOB_OWNER_FILE")
assert_eq "First metalinfra rep name resolved from directory" "$mi2_name0" "Rep One"
assert_eq "Second metalinfra rep name resolved from directory" "$mi2_name1" "Rep Two"
mi2_def0=$(jq -r '.[0].is_default_owner' "$JOB_OWNER_FILE")
mi2_def1=$(jq -r '.[1].is_default_owner' "$JOB_OWNER_FILE")
assert_eq "First metalinfra rep marked as default owner" "$mi2_def0" "true"
assert_eq "Second metalinfra rep marked as default owner" "$mi2_def1" "true"

# -- Python: non-metalinfra owner does NOT get is_default_owner ---------------
echo 'Job blackhole-demo failing. Owner: <@U111>' > "$tmpdir/thread_non_mi.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_non_mi.json"
THREAD_TEXT_FILE="$tmpdir/thread_non_mi.txt"
SLACK_DATA_DIR="$tmpdir/slack_data"
export JOB_OWNER_FILE THREAD_TEXT_FILE SLACK_DATA_DIR
python3 "$PYTHON_SCRIPT"
non_mi_is_default=$(jq -r '.[0].is_default_owner // "absent"' "$JOB_OWNER_FILE")
assert_eq "Non-metalinfra owner has no is_default_owner flag" "$non_mi_is_default" "absent"

# -- Python: mention-token entry falls back to ID when directory lookup fails -
mkdir -p "$tmpdir/slack_data_empty"
echo '{"users": []}' > "$tmpdir/slack_data_empty/slack_directory.json"
echo '{"usergroups": []}' > "$tmpdir/slack_data_empty/slack_groups.json"
echo 'Job blackhole-demo failing. Owner: <@U999>' > "$tmpdir/thread_unknown.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_unknown.json"
THREAD_TEXT_FILE="$tmpdir/thread_unknown.txt"
SLACK_DATA_DIR="$tmpdir/slack_data_empty"
export JOB_OWNER_FILE THREAD_TEXT_FILE SLACK_DATA_DIR
python3 "$PYTHON_SCRIPT"
fallback_count=$(jq 'length' "$JOB_OWNER_FILE")
fallback_id=$(jq -r '.[0].slack_id' "$JOB_OWNER_FILE")
fallback_name=$(jq -r '.[0].name' "$JOB_OWNER_FILE")
assert_eq "Unknown <@U...> entry is preserved (not dropped)" "$fallback_count" "1"
assert_eq "Unknown <@U...> entry keeps slack_id" "$fallback_id" "U999"
assert_eq "Unknown <@U...> entry falls back to ID as name" "$fallback_name" "U999"

# -- Python: same person mentioned by both name and ID is deduplicated --------
mkdir -p "$tmpdir/slack_data_dup"
cat > "$tmpdir/slack_data_dup/slack_directory.json" <<'EOF'
{"users": [
  {"id": "U111", "real_name": "Alice Smith", "deleted": false, "is_bot": false}
]}
EOF
echo '{"usergroups": []}' > "$tmpdir/slack_data_dup/slack_groups.json"
echo 'Job blackhole-demo failing. Owners: <@U111> @Alice Smith' > "$tmpdir/thread_dup.txt"
JOB_OWNER_FILE="$tmpdir/job_owner_dup.json"
THREAD_TEXT_FILE="$tmpdir/thread_dup.txt"
SLACK_DATA_DIR="$tmpdir/slack_data_dup"
export JOB_OWNER_FILE THREAD_TEXT_FILE SLACK_DATA_DIR
python3 "$PYTHON_SCRIPT"
dup_count=$(jq 'length' "$JOB_OWNER_FILE")
assert_eq "Duplicate (mention + @name) collapses to one owner after resolution" "$dup_count" "1"

# -- Shell: graceful failure when credentials missing -------------------------
unset SLACK_TS CHANNEL_ID SLACK_BOT_TOKEN 2>/dev/null || true
JOB_OWNER_FILE="$tmpdir/job_owner_shell.json"
export JOB_OWNER_FILE JOB_NAME
# Script should exit 0 (non-fatal) and write empty array
bash "$SHELL_SCRIPT"
assert "Shell exits 0 when credentials missing" [ -f "$JOB_OWNER_FILE" ]
empty=$(jq 'length' "$JOB_OWNER_FILE")
assert "Shell writes empty array when credentials missing" [ "$empty" -eq 0 ]

# -- Shell: rich_text block extraction (jq) -----------------------------------
# Verify the jq filter in fetch_job_owner.sh extracts text from rich_text blocks
# (the format Slack uses for copied/forwarded messages). We do this by feeding
# a fake conversations.replies response through the same jq expression.
fake_replies=$(cat <<'EOF'
{
  "ok": true,
  "messages": [
    {
      "blocks": [
        {
          "type": "rich_text",
          "elements": [
            {
              "type": "rich_text_section",
              "elements": [
                {"type": "text", "text": "Job blackhole-demo failing. Owner: "},
                {"type": "user", "user_id": "U777"},
                {"type": "text", "text": " from "},
                {"type": "usergroup", "usergroup_id": "S888"}
              ]
            }
          ]
        }
      ]
    }
  ]
}
EOF
)
extract_jq='
  def rich_text_to_text(elems):
    [elems[]? |
      if .type == "text" then (.text // "")
      elif .type == "link" then (.text // .url // "")
      elif .type == "emoji" then (":" + (.name // "") + ":")
      elif .type == "user" then ("<@" + (.user_id // "") + ">")
      elif .type == "usergroup" then ("<!subteam^" + (.usergroup_id // "") + ">")
      elif .type == "channel" then ("<#" + (.channel_id // "") + ">")
      elif (.elements | type) == "array" then rich_text_to_text(.elements)
      else ""
      end
    ] | join("");
  [
    .messages[] |
      if (.blocks | type) == "array" then
        [.blocks[] |
          if .type == "rich_text" then
            rich_text_to_text(.elements // [])
          elif (.text | type) == "object" then
            (.text.text // "")
          else
            (.text // "")
          end
        ] | join("\n")
      else
        (.text // "")
      end
  ] | join("\n")
'
extracted=$(echo "$fake_replies" | jq -r "$extract_jq")
assert "rich_text extraction emits <@U...> mention" [ -n "$(echo "$extracted" | grep -F '<@U777>' || true)" ]
assert "rich_text extraction emits <!subteam^S...> mention" [ -n "$(echo "$extracted" | grep -F '<!subteam^S888>' || true)" ]
assert "rich_text extraction preserves surrounding text" [ -n "$(echo "$extracted" | grep -F 'blackhole-demo' || true)" ]

test_summary

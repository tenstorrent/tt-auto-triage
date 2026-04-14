#!/bin/bash
#
# Tests for scripts/resolve_group_pings.py
#
# Run: cd .github/actions/slack-report-auto-triage && bash tests/slack_report/resolve_group_pings_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/slack-report-auto-triage"
SCRIPTS_DIR="$AT_ROOT/scripts"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"

RESOLVE_SCRIPT="$SCRIPTS_DIR/resolve_group_pings.py"

echo "=== resolve_group_pings ==="

assert "resolve_group_pings.py exists" [ -f "$RESOLVE_SCRIPT" ]

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# Helper: check if a string matches a regex
matches_regex() { [[ "$1" =~ $2 ]]; }

# Helper: invoke resolver via stdin JSON (matches production interface)
run_resolver() {
  local groups_file="$1"
  local directory_file="$2"
  shift 2
  local files_json
  files_json=$(printf '%s\n' "$@" | jq -R . | jq -s .)
  jq -n \
    --arg groups "$groups_file" \
    --arg directory "$directory_file" \
    --argjson files "$files_json" \
    '{slack_groups: $groups, slack_directory: $directory, files: $files}' \
  | python3 "$RESOLVE_SCRIPT" || { echo "FAIL: resolver exited non-zero"; exit 1; }
}

# -- Fixture: slack_groups.json with member lists ------------------------------
cat > "$tmpdir/slack_groups.json" <<'EOF'
{
  "generated_at": "2025-01-01T00:00:00Z",
  "usergroups": [
    {"id": "S111GROUP", "handle": "metalinfra", "name": "Metal Infra", "description": "", "users": ["U_ALICE", "U_BOB", "U_CAROL"]},
    {"id": "S222EMPTY", "handle": "empty-team", "name": "Empty Team", "description": "", "users": []},
    {"id": "S333BOTS",  "handle": "bot-team",   "name": "Bot Team",   "description": "", "users": ["U_BOTONLY"]}
  ]
}
EOF

# -- Fixture: slack_directory.json with user data ------------------------------
cat > "$tmpdir/slack_directory.json" <<'EOF'
{
  "generated_at": "2025-01-01T00:00:00Z",
  "users": [
    {"id": "U_ALICE", "display_name": "Alice Smith", "real_name": "Alice Smith", "username": "alice", "email": "alice@tt.com", "is_bot": false, "deleted": false},
    {"id": "U_BOB",   "display_name": "Bob Jones",   "real_name": "Bob Jones",   "username": "bob",   "email": "bob@tt.com",   "is_bot": false, "deleted": false},
    {"id": "U_CAROL", "display_name": "Carol Wu",    "real_name": "Carol Wu",    "username": "carol", "email": "carol@tt.com", "is_bot": false, "deleted": true},
    {"id": "U_BOTONLY","display_name": "CI Bot",      "real_name": "CI Bot",      "username": "cibot", "email": "",             "is_bot": true,  "deleted": false}
  ]
}
EOF

# -- Test 1: S-prefixed ID in relevant_developers is resolved -----------------
cat > "$tmpdir/msg1.json" <<'EOF'
{
  "case": "4",
  "relevant_developers": [
    {"name": "Metal Infra", "slack_id": "S111GROUP"},
    {"name": "Dave Human", "slack_id": "U_DAVE"}
  ]
}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg1.json"

resolved_id=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg1.json")
resolved_name=$(jq -r '.relevant_developers[0].name' "$tmpdir/msg1.json")
untouched_id=$(jq -r '.relevant_developers[1].slack_id' "$tmpdir/msg1.json")

assert "Group S-ID replaced with U-ID" matches_regex "$resolved_id" "^U_"
assert "Resolved name contains 'representing'" matches_regex "$resolved_name" "representing Metal Infra"
assert_eq "Non-group entry unchanged" "$untouched_id" "U_DAVE"
assert "Resolved to Alice or Bob (not deleted Carol)" matches_regex "$resolved_id" "^U_(ALICE|BOB)$"

# -- Test 2: Empty group falls back to plain name (no resolution) -------------
cat > "$tmpdir/msg2.json" <<'EOF'
{
  "case": "2",
  "relevant_developers": [
    {"name": "Empty Team", "slack_id": "S222EMPTY"}
  ]
}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg2.json"

empty_id=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg2.json")
assert_eq "Empty group keeps S-prefixed ID" "$empty_id" "S222EMPTY"

# -- Test 3: All-bots group falls back to plain name --------------------------
cat > "$tmpdir/msg3.json" <<'EOF'
{
  "case": "2",
  "relevant_developers": [
    {"name": "Bot Team", "slack_id": "S333BOTS"}
  ]
}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg3.json"

bots_id=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg3.json")
assert_eq "All-bots group keeps S-prefixed ID" "$bots_id" "S333BOTS"

# -- Test 4: Nested person objects in commits are resolved --------------------
cat > "$tmpdir/msg4.json" <<'EOF'
{
  "case": "4",
  "commits": [
    {
      "hash": "abc123",
      "author": {"name": "Ethan", "slack_id": "U_ETHAN"},
      "approvers": [{"name": "Metal Infra", "slack_id": "S111GROUP"}],
      "relevant_developers": [{"name": "Metal Infra", "slack_id": "S111GROUP"}]
    }
  ],
  "relevant_developers": []
}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg4.json"

approver_id=$(jq -r '.commits[0].approvers[0].slack_id' "$tmpdir/msg4.json")
commit_dev_id=$(jq -r '.commits[0].relevant_developers[0].slack_id' "$tmpdir/msg4.json")
author_id=$(jq -r '.commits[0].author.slack_id' "$tmpdir/msg4.json")

assert "Commit approver group resolved" matches_regex "$approver_id" "^U_(ALICE|BOB)$"
assert "Commit relevant_dev group resolved" matches_regex "$commit_dev_id" "^U_(ALICE|BOB)$"
assert_eq "Commit author U-ID untouched" "$author_id" "U_ETHAN"

# -- Test 5: job_owner.json flat array is resolved ----------------------------
cat > "$tmpdir/job_owner.json" <<'EOF'
[
  {"name": "Metal Infra", "slack_id": "S111GROUP"},
  {"name": "Human Dev", "slack_id": "U_HUMAN"}
]
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/job_owner.json"

owner_id=$(jq -r '.[0].slack_id' "$tmpdir/job_owner.json")
human_id=$(jq -r '.[1].slack_id' "$tmpdir/job_owner.json")

assert "Job owner group resolved" matches_regex "$owner_id" "^U_(ALICE|BOB)$"
assert_eq "Job owner human unchanged" "$human_id" "U_HUMAN"

# -- Test 6: Missing groups file is non-fatal ---------------------------------
cat > "$tmpdir/msg6.json" <<'EOF'
{"relevant_developers": [{"name": "Team", "slack_id": "S111GROUP"}]}
EOF

run_resolver "$tmpdir/nonexistent_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg6.json"

still_s=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg6.json")
assert_eq "Missing groups file: ID unchanged" "$still_s" "S111GROUP"

# -- Test 7: Missing directory file is non-fatal ------------------------------
cat > "$tmpdir/msg7.json" <<'EOF'
{"relevant_developers": [{"name": "Team", "slack_id": "S111GROUP"}]}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/nonexistent_directory.json" "$tmpdir/msg7.json"

still_s7=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg7.json")
assert_eq "Missing directory file: ID unchanged" "$still_s7" "S111GROUP"

# -- Test 8: Unknown group ID is non-fatal ------------------------------------
cat > "$tmpdir/msg8.json" <<'EOF'
{"relevant_developers": [{"name": "Mystery", "slack_id": "S999UNKNOWN"}]}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg8.json"

unknown_id=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg8.json")
assert_eq "Unknown group: ID unchanged" "$unknown_id" "S999UNKNOWN"

# -- Test 9: Multiple files processed in one call ----------------------------
cat > "$tmpdir/multi_a.json" <<'EOF'
{"relevant_developers": [{"name": "Metal Infra", "slack_id": "S111GROUP"}]}
EOF
cat > "$tmpdir/multi_b.json" <<'EOF'
[{"name": "Metal Infra", "slack_id": "S111GROUP"}]
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/multi_a.json" "$tmpdir/multi_b.json"

multi_a_id=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/multi_a.json")
multi_b_id=$(jq -r '.[0].slack_id' "$tmpdir/multi_b.json")

assert "Multi-file: first file resolved" matches_regex "$multi_a_id" "^U_(ALICE|BOB)$"
assert "Multi-file: second file resolved" matches_regex "$multi_b_id" "^U_(ALICE|BOB)$"


# -- Test 10: Missing target file is skipped with a warning -------------------
# process_file() should not fail when a file in the list doesn't exist
cat > "$tmpdir/msg10_real.json" <<'EOF'
{"relevant_developers": [{"name": "Metal Infra", "slack_id": "S111GROUP"}]}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg10_real.json" "$tmpdir/nonexistent_target.json"

real_id=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg10_real.json")
assert "Missing target file: real file still processed" matches_regex "$real_id" "^U_(ALICE|BOB)$"

# -- Test 11: Malformed JSON target file is skipped, others still processed ---
echo '{broken json' > "$tmpdir/msg11_bad.json"
cat > "$tmpdir/msg11_good.json" <<'EOF'
{"relevant_developers": [{"name": "Metal Infra", "slack_id": "S111GROUP"}]}
EOF

run_resolver "$tmpdir/slack_groups.json" "$tmpdir/slack_directory.json" "$tmpdir/msg11_bad.json" "$tmpdir/msg11_good.json"

good_id=$(jq -r '.relevant_developers[0].slack_id' "$tmpdir/msg11_good.json")
# Bad file should be unchanged (still bad JSON)
bad_still_bad=$(cat "$tmpdir/msg11_bad.json")
assert "Malformed target: good file still processed" matches_regex "$good_id" "^U_(ALICE|BOB)$"
assert_eq "Malformed target: bad file left unchanged" "$bad_still_bad" "{broken json"

# -- Test 12: All groups have empty users lists triggers a warning -------------
cat > "$tmpdir/no_users_groups.json" <<'EOF'
{"usergroups": [{"id": "S111GROUP", "handle": "metalinfra", "name": "Metal Infra", "users": []}]}
EOF
cat > "$tmpdir/msg12.json" <<'EOF'
{"relevant_developers": [{"name": "Metal Infra", "slack_id": "S111GROUP"}]}
EOF

# Capture stderr to check for the warning message
resolver_stderr=$(jq -n \
    --arg groups "$tmpdir/no_users_groups.json" \
    --arg directory "$tmpdir/slack_directory.json" \
    --argjson files "["$tmpdir/msg12.json"]" \
    '{slack_groups: $groups, slack_directory: $directory, files: $files}' \
  | python3 "$RESOLVE_SCRIPT" 2>&1 1>/dev/null)
assert "Empty users warning emitted" matches_regex "$resolver_stderr" "empty member lists"

test_summary

#!/usr/bin/env bash
set -euo pipefail

# verify.sh — Bug Escape Verification System
#
# Completely separate from the detection pipeline (Phases 1-4).
# Takes a specific bug escape prediction as input and validates it by:
#   1. Creating before/after branches via GitHub API (no git push needed)
#   2. Pruning the test matrix YAML via GitHub Contents API
#   3. Dispatching workflow runs on both branches
#   4. Polling until complete
#   5. Comparing results to confirm or refute the prediction
#
# Required environment variables:
#   FIX_COMMIT_SHA    — the commit believed to have fixed the bug
#   TEST_PIPELINE     — workflow file (e.g. .github/workflows/galaxy-e2e-tests.yaml)
#   TEST_JOB          — job display name (e.g. "BH Galaxy CCL tests")
#   TEST_NAME         — full pytest path (e.g. tests/.../test_foo.py::test_bar)
#   REPO_DIR          — path to the tt-metal checkout (used for YAML discovery only)
#   GITHUB_TOKEN      — token with contents:write and actions:write on OWNER_REPO
#
# Optional:
#   POLL_INTERVAL          — seconds between status checks (default: 120)
#   MAX_WAIT_START_MINUTES — max wait for runs to leave queued (default: 240)
#   MAX_WAIT_FINISH_MINUTES — max wait for runs to complete once started (default: 120)
#   OWNER_REPO        — GitHub owner/repo (default: tenstorrent/tt-metal)
#   CURSOR_API_KEY    — Cursor AI API key for failure classification
#   EXPECTED_FAILURE_SIG — expected failure signature from the bug escape

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/verify_common.sh"

# ---- Input validation ----

FIX_COMMIT_SHA="${FIX_COMMIT_SHA:?FIX_COMMIT_SHA is required}"
TEST_PIPELINE="${TEST_PIPELINE:?TEST_PIPELINE is required}"
TEST_JOB="${TEST_JOB:?TEST_JOB is required}"
TEST_NAME="${TEST_NAME:?TEST_NAME is required}"
REPO_DIR="${REPO_DIR:?REPO_DIR is required}"
GITHUB_TOKEN="${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
POLL_INTERVAL="${POLL_INTERVAL:-120}"
MAX_WAIT_START_MINUTES="${MAX_WAIT_START_MINUTES:-240}"
MAX_WAIT_FINISH_MINUTES="${MAX_WAIT_FINISH_MINUTES:-120}"
OWNER_REPO="${OWNER_REPO:-tenstorrent/tt-metal}"
CURSOR_API_KEY="${CURSOR_API_KEY:-}"
EXPECTED_FAILURE_SIG="${EXPECTED_FAILURE_SIG:-}"

MOCK_VERIFY="${MOCK_VERIFY:-false}"
DRY_RUN="${DRY_RUN:-false}"

# ---- Mock mode (no hardware dispatches) ----
if [ "$MOCK_VERIFY" = "true" ]; then
  mkdir -p "$VERIFY_OUTPUT_DIR"
  verify_info "MOCK_VERIFY=true — skipping actual CI dispatch, writing mock confirmed result"
  write_result "confirmed" "mock verification (MOCK_VERIFY=true — no hardware runs dispatched)"     "failure" "success" 0 0
  exit 0
fi

# B4: Dry-run mode — print what would be dispatched without doing it.
# Useful for pre-validating the verification queue before burning CI slots.
if [ "$DRY_RUN" = "true" ] || [ "$DRY_RUN" = "1" ]; then
  verify_info "DRY_RUN=true — printing dispatch plan without executing"
  SHORT_SHA_DRY="${FIX_COMMIT_SHA:0:8}"
  echo "=== DRY RUN: Bug Escape Verification Plan ==="
  echo "  Fix commit:   $FIX_COMMIT_SHA"
  echo "  Test pipeline: $TEST_PIPELINE"
  echo "  Test job:      $TEST_JOB"
  echo "  Test name:     $TEST_NAME"
  echo "  Repo:          $OWNER_REPO"
  echo "  Branch BEFORE: verify-${SHORT_SHA_DRY}-before  (parent of fix commit — expects FAILURE)"
  echo "  Branch AFTER:  verify-${SHORT_SHA_DRY}-after   (at fix commit — expects PASS)"
  echo "  Poll interval: ${POLL_INTERVAL}s"
  echo "  Expected duration: ~$(( (MAX_WAIT_START_MINUTES + MAX_WAIT_FINISH_MINUTES) / 60 + 1 )) hours"
  echo "  GH API calls (estimated): ~28"
  echo "=== END DRY RUN ==="
  exit 0
fi

mkdir -p "$VERIFY_OUTPUT_DIR"

SHORT_SHA="${FIX_COMMIT_SHA:0:8}"
BRANCH_BEFORE="verify-${SHORT_SHA}-before"
BRANCH_AFTER="verify-${SHORT_SHA}-after"

# ---- GitHub API helpers (avoid git push / workflow scope issues) ----

api_delete_branch() {
  local branch="$1"
  curl -sf -X DELETE \
    -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/$OWNER_REPO/git/refs/heads/$branch" \
    2>/dev/null || true
}

api_create_or_reset_branch() {
  local branch="$1" sha="$2"
  # Try create first; if it exists (422), force-update instead
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: token $GITHUB_TOKEN" \
    -H "Content-Type: application/json" \
    "https://api.github.com/repos/$OWNER_REPO/git/refs" \
    -d "{\"ref\": \"refs/heads/$branch\", \"sha\": \"$sha\"}")

  if [ "$http_code" = "422" ]; then
    # Branch already exists — force update
    curl -sf -X PATCH \
      -H "Authorization: token $GITHUB_TOKEN" \
      -H "Content-Type: application/json" \
      "https://api.github.com/repos/$OWNER_REPO/git/refs/heads/$branch" \
      -d "{\"sha\": \"$sha\", \"force\": true}" > /dev/null
  elif [ "$http_code" != "201" ]; then
    verify_error "Failed to create branch $branch (HTTP $http_code)"
    return 1
  fi
}

api_get_file_content() {
  # Fetch the raw (decoded) content of a file on a branch.
  local branch="$1" file_path="$2"
  curl -sf \
    -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/$OWNER_REPO/contents/$file_path?ref=$branch" \
    | python3 -c "import sys,json,base64; d=json.load(sys.stdin); print(base64.b64decode(d['content']).decode())"
}

api_update_file() {
  local branch="$1" file_path="$2" content="$3"

  # Get current file SHA on the branch (may return empty if file is new in fix commit)
  local file_sha api_resp
  api_resp=$(curl -s \
    -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/$OWNER_REPO/contents/$file_path?ref=$branch" 2>/dev/null || echo "")
  file_sha=$(echo "$api_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sha',''))" 2>/dev/null || echo "")

  # Base64-encode content (no line wrapping — API expects unwrapped)
  local encoded_content
  encoded_content=$(echo "$content" | base64 -w0)

  # Build JSON payload: include sha only when updating an existing file
  local payload
  if [ -n "$file_sha" ]; then
    payload="{
      \"message\": \"verify: prune test matrix for bug escape verification\",
      \"content\": \"$encoded_content\",
      \"sha\": \"$file_sha\",
      \"branch\": \"$branch\"
    }"
  else
    # File doesn't exist on this branch (e.g. was added by the fix commit) — create it
    verify_info "api_update_file: $file_path not found on $branch, creating new file"
    payload="{
      \"message\": \"verify: create test matrix for bug escape verification\",
      \"content\": \"$encoded_content\",
      \"branch\": \"$branch\"
    }"
  fi

  local result
  result=$(curl -sf -X PUT \
    -H "Authorization: token $GITHUB_TOKEN" \
    -H "Content-Type: application/json" \
    "https://api.github.com/repos/$OWNER_REPO/contents/$file_path" \
    -d "$payload")

  echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print('Updated', r.get('commit',{}).get('sha','?'))"
}

cleanup_branches() {
  verify_info "Cleaning up verification branches"
  api_delete_branch "$BRANCH_BEFORE"
  api_delete_branch "$BRANCH_AFTER"
}

write_result() {
  local verdict="$1" reason="$2"
  local before_conclusion="${3:-unknown}" after_conclusion="${4:-unknown}"
  local before_run_id="${5:-0}" after_run_id="${6:-0}"

  local result_file="$VERIFY_OUTPUT_DIR/verification-result.json"

  jq -n \
    --arg fix "$FIX_COMMIT_SHA" \
    --arg pipeline "$TEST_PIPELINE" \
    --arg job "$TEST_JOB" \
    --arg test "$TEST_NAME" \
    --arg verdict "$verdict" \
    --arg reason "$reason" \
    --arg bc "$before_conclusion" \
    --arg ac "$after_conclusion" \
    --argjson brid "$before_run_id" \
    --argjson arid "$after_run_id" \
    --arg ts "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    '{
      "fix_commit": $fix,
      "test_pipeline": $pipeline,
      "test_job": $job,
      "test_name": $test,
      "before_run_id": $brid,
      "before_conclusion": $bc,
      "after_run_id": $arid,
      "after_conclusion": $ac,
      "verdict": $verdict,
      "reason": $reason,
      "timestamp": $ts
    }' > "$result_file"

  verify_info "Verdict: $verdict — $reason"
  verify_info "Result written to $result_file"

  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "## Bug Escape Verification"
      echo ""
      echo "| Field | Value |"
      echo "|-------|-------|"
      echo "| Fix commit | \`$FIX_COMMIT_SHA\` |"
      echo "| Test pipeline | $TEST_PIPELINE |"
      echo "| Test job | $TEST_JOB |"
      echo "| Test name | \`$TEST_NAME\` |"
      echo "| Before run | $before_run_id ($before_conclusion) |"
      echo "| After run | $after_run_id ($after_conclusion) |"
      echo "| **Verdict** | **$verdict** |"
      echo "| Reason | $reason |"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
}

# ---- Fault-tolerance: guarantee an artifact on any exit path ----
# If verify.sh dies from set -euo pipefail (or any unexpected error) before
# write_result is called, the matrix leg still needs to upload a stub so the
# aggregator doesn't see a gap. This trap writes a minimal inconclusive
# verification-result.json if one doesn't already exist and attempts branch
# cleanup. All referenced helpers (write_result, cleanup_branches) are
# defined above this point. Runs on every exit path (clean or error).
_verify_exit_trap() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ ! -f "$VERIFY_OUTPUT_DIR/verification-result.json" ]; then
    if ! write_result "inconclusive" "verify.sh exited unexpectedly (rc=$rc) before a verdict was written" 2>/dev/null; then
      jq -n \
        --arg fix  "${FIX_COMMIT_SHA:-unknown}" \
        --arg pipe "${TEST_PIPELINE:-unknown}" \
        --arg job  "${TEST_JOB:-unknown}" \
        --arg test "${TEST_NAME:-unknown}" \
        --arg rc   "$rc" \
        --arg ts   "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        '{
          fix_commit: $fix,
          test_pipeline: $pipe,
          test_job: $job,
          test_name: $test,
          before_run_id: 0,
          before_conclusion: "unknown",
          after_run_id: 0,
          after_conclusion: "unknown",
          verdict: "inconclusive",
          reason: ("verify.sh exited unexpectedly (rc=" + $rc + ") before write_result"),
          timestamp: $ts
        }' > "$VERIFY_OUTPUT_DIR/verification-result.json" 2>/dev/null || true
    fi
  fi
  cleanup_branches 2>/dev/null || true
}
trap _verify_exit_trap EXIT

# ---- Step 1: Resolve parent commit ----

verify_info "=== Bug Escape Verification ==="
verify_info "Fix commit: $FIX_COMMIT_SHA"
verify_info "Test: $TEST_NAME"
verify_info "Job: $TEST_JOB"
verify_info "Pipeline: $TEST_PIPELINE"

cd "$REPO_DIR"

PARENT_SHA=$(git rev-parse "${FIX_COMMIT_SHA}^" 2>/dev/null || echo "")
if [ -z "$PARENT_SHA" ]; then
  verify_info "Fetching commit from remote..."
  git fetch origin "$FIX_COMMIT_SHA" --depth=2 2>/dev/null || true
  PARENT_SHA=$(git rev-parse "${FIX_COMMIT_SHA}^" 2>/dev/null || echo "")
fi

# GitHub API fallback for parent SHA
if [ -z "$PARENT_SHA" ] || [[ "$PARENT_SHA" == *"^"* ]]; then
  verify_info "Using GitHub API to resolve parent SHA..."
  PARENT_SHA=$(curl -sf -H "Authorization: Bearer $GITHUB_TOKEN" \
    "https://api.github.com/repos/$OWNER_REPO/commits/$FIX_COMMIT_SHA" \
    | python3 -c "import sys,json; c=json.load(sys.stdin); print(c['parents'][0]['sha'])" 2>/dev/null || echo "")
fi

if [ -z "$PARENT_SHA" ]; then
  write_result "inconclusive" "Could not resolve parent of $FIX_COMMIT_SHA"
  exit 1
fi

verify_info "Parent commit: $PARENT_SHA"

# ---- Step 2: Discover test YAML path ----

WF_BASENAME=$(basename "$TEST_PIPELINE")
TESTS_YAML_PATH=$(discover_tests_yaml_path "$WF_BASENAME" "$REPO_DIR" "$TEST_JOB")

# Detect if the discovered path is a workflow impl YAML (inline test matrix)
# vs a standard tests-list YAML. Impl YAMLs live under .github/workflows/
# and end with -impl.yaml.
TESTS_YAML_IS_IMPL=false
if [[ "$TESTS_YAML_PATH" == .github/workflows/*-impl.yaml ]]; then
  TESTS_YAML_IS_IMPL=true
fi
export TESTS_YAML_IS_IMPL
if [ -z "$TESTS_YAML_PATH" ]; then
  write_result "inconclusive" "Could not discover TESTS_YAML_PATH for $TEST_PIPELINE"
  exit 1
fi

verify_info "Tests YAML: $TESTS_YAML_PATH"

# ---- Step 3: Find the original test entry and derive SKU flags ----

TEST_ENTRY_JSON=$(find_test_entry "$TEST_JOB" "$TESTS_YAML_PATH" "$REPO_DIR")
if [ -z "$TEST_ENTRY_JSON" ] || [ "$TEST_ENTRY_JSON" = "null" ]; then
  write_result "inconclusive" "Could not find test entry for job '$TEST_JOB' in $TESTS_YAML_PATH"
  exit 1
fi

verify_info "Found test entry: $(echo "$TEST_ENTRY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name','?'))")"

SKU_FLAGS=$(derive_sku_flags "$TEST_ENTRY_JSON" "$WF_BASENAME" "$REPO_DIR")
verify_info "SKU flags: $SKU_FLAGS"

# ---- Step 4: Build pruned YAML ----

PRUNED_YAML=$(build_pruned_yaml "$TEST_JOB" "$TEST_NAME" "$TEST_ENTRY_JSON")
verify_info "Pruned YAML generated ($(echo "$PRUNED_YAML" | wc -l) lines)"

# ---- Step 5: Create branches and update file via GitHub API ----
# (Using the API avoids git push, which requires 'workflow' PAT scope
#  even when the pushed files are not in .github/workflows/)

cleanup_branches

# Helper: update TESTS_YAML_PATH on a branch with either:
#   - The pruned test list (normal mode), or
#   - A surgically modified workflow that replaces only the heredoc section (impl mode)
_apply_pruned_yaml_to_branch() {
  local branch="$1" base_sha="$2"
  api_create_or_reset_branch "$branch" "$base_sha"
  verify_info "Updating $TESTS_YAML_PATH on $branch"

  if [ "$TESTS_YAML_IS_IMPL" = "true" ]; then
    # Impl-mode: fetch the current full workflow file and replace only the
    # inline test heredoc, preserving the rest of the GHA workflow structure.
    local _tmp_content _tmp_entry _tmp_out
    _tmp_content=$(mktemp)
    _tmp_entry=$(mktemp)
    _tmp_out=$(mktemp)
    api_get_file_content "$branch" "$TESTS_YAML_PATH" > "$_tmp_content"
    echo "$TEST_ENTRY_JSON" > "$_tmp_entry"
    build_pruned_impl_workflow_content "$_tmp_content" "$_tmp_entry" > "$_tmp_out"
    local modified_content
    modified_content=$(cat "$_tmp_out")
    rm -f "$_tmp_content" "$_tmp_entry" "$_tmp_out"
    api_update_file "$branch" "$TESTS_YAML_PATH" "$modified_content"
  else
    api_update_file "$branch" "$TESTS_YAML_PATH" "$PRUNED_YAML"
  fi
}

verify_info "Creating branch $BRANCH_BEFORE at $PARENT_SHA"
_apply_pruned_yaml_to_branch "$BRANCH_BEFORE" "$PARENT_SHA"

verify_info "Creating branch $BRANCH_AFTER at $FIX_COMMIT_SHA"
_apply_pruned_yaml_to_branch "$BRANCH_AFTER" "$FIX_COMMIT_SHA"

# ---- Step 6: Dispatch workflow runs ----

verify_info "Dispatching BEFORE run on $BRANCH_BEFORE"
gh workflow run "$WF_BASENAME" --ref "$BRANCH_BEFORE" $SKU_FLAGS || {
  write_result "inconclusive" "Failed to dispatch BEFORE run"
  cleanup_branches
  exit 1
}

verify_info "Dispatching AFTER run on $BRANCH_AFTER"
gh workflow run "$WF_BASENAME" --ref "$BRANCH_AFTER" $SKU_FLAGS || {
  write_result "inconclusive" "Failed to dispatch AFTER run"
  cleanup_branches
  exit 1
}

sleep 15

# ---- Step 7: Find the run IDs ----

BEFORE_RUN_ID=$(wait_for_run_to_appear "$BRANCH_BEFORE" "$WF_BASENAME" 20)
if [ -z "$BEFORE_RUN_ID" ]; then
  write_result "inconclusive" "Could not find BEFORE run after dispatch" "unknown" "unknown" 0 0
  cleanup_branches
  exit 1
fi

AFTER_RUN_ID=$(wait_for_run_to_appear "$BRANCH_AFTER" "$WF_BASENAME" 20)
if [ -z "$AFTER_RUN_ID" ]; then
  write_result "inconclusive" "Could not find AFTER run after dispatch" "unknown" "unknown" "$BEFORE_RUN_ID" 0
  cleanup_branches
  exit 1
fi

verify_info "BEFORE run: $BEFORE_RUN_ID"
verify_info "AFTER run: $AFTER_RUN_ID"

# ---- Step 8a: Wait for runs to start (leave queued) ----

if ! poll_run_start "$BEFORE_RUN_ID" "$MAX_WAIT_START_MINUTES"; then
  write_result "inconclusive_timeout" "Run $BEFORE_RUN_ID did not start within $MAX_WAIT_START_MINUTES minutes (queued timeout)" \
    "queued_timeout" "unknown" "$BEFORE_RUN_ID" "$AFTER_RUN_ID"
  cleanup_branches
  exit 1
fi

if ! poll_run_start "$AFTER_RUN_ID" "$MAX_WAIT_START_MINUTES"; then
  write_result "inconclusive_timeout" "Run $AFTER_RUN_ID did not start within $MAX_WAIT_START_MINUTES minutes (queued timeout)" \
    "unknown" "queued_timeout" "$BEFORE_RUN_ID" "$AFTER_RUN_ID"
  cleanup_branches
  exit 1
fi

# ---- Step 8b: Poll until both complete ----

BEFORE_CONCLUSION=$(poll_run_completion "$BEFORE_RUN_ID" "$POLL_INTERVAL" "$MAX_WAIT_FINISH_MINUTES")
AFTER_CONCLUSION=$(poll_run_completion "$AFTER_RUN_ID" "$POLL_INTERVAL" "$MAX_WAIT_FINISH_MINUTES")

# ---- Step 8c: Handle timeouts gracefully ----

if [ "$BEFORE_CONCLUSION" = "timed_out" ] || [ "$AFTER_CONCLUSION" = "timed_out" ]; then
  write_result "inconclusive" "One or both runs timed out (before=$BEFORE_CONCLUSION, after=$AFTER_CONCLUSION)" \
    "$BEFORE_CONCLUSION" "$AFTER_CONCLUSION" "${BEFORE_RUN_ID:-0}" "${AFTER_RUN_ID:-0}"
  cleanup_branches
  exit 0  # Don't fail the job, just write inconclusive
fi

# ---- Step 9: Check for "no tests ran" ----

if [ "$BEFORE_CONCLUSION" = "failure" ]; then
  if check_no_tests_ran "$BEFORE_RUN_ID" "$TEST_JOB"; then
    verify_warn "BEFORE run: no tests actually ran — marking inconclusive"
    BEFORE_CONCLUSION="inconclusive_no_tests"
  fi
fi

if [ "$AFTER_CONCLUSION" = "failure" ]; then
  if check_no_tests_ran "$AFTER_RUN_ID" "$TEST_JOB"; then
    verify_warn "AFTER run: no tests actually ran — marking inconclusive"
    AFTER_CONCLUSION="inconclusive_no_tests"
  fi
fi

# ---- Step 9b: AI-based failure classification ----
# For runs still marked "failure" after the no-tests check, use Cursor AI
# to determine if the failure is a real test failure or infra/unrelated.

if [ "$BEFORE_CONCLUSION" = "failure" ]; then
  BEFORE_FAILURE_CLASS=$(check_failure_is_real "$BEFORE_RUN_ID" "$TEST_JOB" "$EXPECTED_FAILURE_SIG" "$CURSOR_API_KEY")
  if [ "$BEFORE_FAILURE_CLASS" != "real_failure" ]; then
    verify_warn "BEFORE run: AI classified as $BEFORE_FAILURE_CLASS — marking inconclusive_infra"
    BEFORE_CONCLUSION="inconclusive_infra"
  fi
fi

if [ "$AFTER_CONCLUSION" = "failure" ]; then
  AFTER_FAILURE_CLASS=$(check_failure_is_real "$AFTER_RUN_ID" "$TEST_JOB" "$EXPECTED_FAILURE_SIG" "$CURSOR_API_KEY")
  if [ "$AFTER_FAILURE_CLASS" != "real_failure" ]; then
    verify_warn "AFTER run: AI classified as $AFTER_FAILURE_CLASS — marking inconclusive_infra"
    AFTER_CONCLUSION="inconclusive_infra"
  fi
fi

# ---- Step 10: Evaluate verdict ----

VERDICT=""
REASON=""

if [[ "$BEFORE_CONCLUSION" == *"inconclusive"* ]] || [[ "$AFTER_CONCLUSION" == *"inconclusive"* ]] || \
   [[ "$BEFORE_CONCLUSION" == "timed_out" ]] || [[ "$AFTER_CONCLUSION" == "timed_out" ]] || \
   [[ "$BEFORE_CONCLUSION" == "cancelled" ]] || [[ "$AFTER_CONCLUSION" == "cancelled" ]]; then
  VERDICT="inconclusive"
  REASON="One or both runs did not produce a usable result (before=$BEFORE_CONCLUSION, after=$AFTER_CONCLUSION)"
elif [ "$BEFORE_CONCLUSION" = "failure" ] && [ "$AFTER_CONCLUSION" = "success" ]; then
  VERDICT="confirmed"
  REASON="Test fails before the commit and passes after — fix attribution is correct"
elif [ "$BEFORE_CONCLUSION" = "success" ] && [ "$AFTER_CONCLUSION" = "success" ]; then
  VERDICT="refuted"
  REASON="Test passes on both sides — the fix was already present before this commit (or test is flaky)"
elif [ "$BEFORE_CONCLUSION" = "failure" ] && [ "$AFTER_CONCLUSION" = "failure" ]; then
  VERDICT="refuted"
  REASON="Test fails on both sides — this commit did not fix the failure"
elif [ "$BEFORE_CONCLUSION" = "success" ] && [ "$AFTER_CONCLUSION" = "failure" ]; then
  VERDICT="refuted"
  REASON="Inverted result: test passes before but fails after — this commit may have introduced a regression"
else
  VERDICT="inconclusive"
  REASON="Unexpected results (before=$BEFORE_CONCLUSION, after=$AFTER_CONCLUSION)"
fi

write_result "$VERDICT" "$REASON" "$BEFORE_CONCLUSION" "$AFTER_CONCLUSION" "$BEFORE_RUN_ID" "$AFTER_RUN_ID"

# ---- Step 11: Cleanup ----

cleanup_branches

verify_info "Verification complete."
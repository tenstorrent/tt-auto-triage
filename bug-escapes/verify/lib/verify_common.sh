#!/usr/bin/env bash
#
# verify_common.sh — Helpers for the bug-escape verification system.
#
# Completely independent from the detection pipeline (Phases 1-4).
# Provides utilities for: test YAML discovery, SKU flag mapping,
# pruned YAML generation, run polling, and "no tests ran" detection.

if [ -n "${_VERIFY_COMMON_LOADED:-}" ]; then
  return 0
fi
_VERIFY_COMMON_LOADED=1

VERIFY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY_OUTPUT_DIR="$VERIFY_ROOT/output"

# Minimal logging (no dependency on auto-triage libs)
verify_info()  { printf '[verify] %s\n' "$*"; }
verify_warn()  { printf '[verify][WARN] %s\n' "$*" >&2; }
verify_error() { printf '[verify][ERR] %s\n' "$*" >&2; }
verify_die()   { verify_error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# discover_tests_yaml_path WORKFLOW_FILE REPO_DIR
#
# Finds the TESTS_YAML_PATH for a given workflow by inspecting its -impl.yaml.
# E.g. galaxy-e2e-tests.yaml -> galaxy-e2e-tests-impl.yaml -> ./tests/pipeline_reorg/galaxy_e2e_tests.yaml
# Returns the relative path (from repo root) on stdout.
# ---------------------------------------------------------------------------
discover_tests_yaml_path() {
  local wf_file="$1" repo_dir="$2"
  local wf_basename impl_path tests_yaml_path

  wf_basename=$(basename "$wf_file" .yaml)
  impl_path="$repo_dir/.github/workflows/${wf_basename}-impl.yaml"

  if [ ! -f "$impl_path" ]; then
    verify_warn "Impl workflow not found: $impl_path"
    return 1
  fi

  tests_yaml_path=$(grep -E '^\s*TESTS_YAML_PATH:' "$impl_path" \
    | head -1 \
    | sed 's/.*TESTS_YAML_PATH:\s*//' \
    | sed 's/^[[:space:]]*//' \
    | sed 's/[[:space:]]*$//')

  if [ -z "$tests_yaml_path" ]; then
    verify_warn "TESTS_YAML_PATH not found in $impl_path"
    return 1
  fi

  # Strip leading ./
  tests_yaml_path="${tests_yaml_path#./}"
  echo "$tests_yaml_path"
}

# ---------------------------------------------------------------------------
# find_test_entry TEST_JOB TESTS_YAML_PATH REPO_DIR
#
# Finds the YAML entry in the test matrix whose 'name' matches TEST_JOB.
# Outputs the full entry as JSON on stdout.
# Requires: python3 + PyYAML (available in CI, and locally via pip).
# ---------------------------------------------------------------------------
find_test_entry() {
  local test_job="$1" tests_yaml_path="$2" repo_dir="$3"
  local full_path="$repo_dir/$tests_yaml_path"

  if [ ! -f "$full_path" ]; then
    verify_warn "Tests YAML not found: $full_path"
    return 1
  fi

  python3 -c "
import yaml, json, sys
with open('$full_path') as f:
    tests = yaml.safe_load(f)
target = '''$test_job'''
# Pass 1: exact match
for entry in tests:
    if entry.get('name', '') == target:
        print(json.dumps(entry))
        sys.exit(0)
# Pass 2: target is the suffix after ' / ' (GitHub Actions adds workflow prefix)
stripped = target.split(' / ')[-1] if ' / ' in target else target
for entry in tests:
    if entry.get('name', '') == stripped:
        print(json.dumps(entry))
        sys.exit(0)
# Pass 3: substring match (prefer longer name matches to avoid WH matching before BH)
matches = []
for entry in tests:
    name = entry.get('name', '')
    if target in name or name in target:
        matches.append((len(name), entry))
if matches:
    matches.sort(key=lambda x: -x[0])
    print(json.dumps(matches[0][1]))
    sys.exit(0)
print('null')
sys.exit(1)
" 2>/dev/null
}

# ---------------------------------------------------------------------------
# derive_sku_flags TEST_ENTRY_JSON WORKFLOW_FILE REPO_DIR
#
# Given a test entry (JSON) and workflow, determine the workflow_dispatch flags
# needed to run only the matching SKU. Outputs space-separated -f flags.
# E.g.: "-f blackhole=true -f wormhole=false"
# ---------------------------------------------------------------------------
derive_sku_flags() {
  local entry_json="$1" wf_file="$2" repo_dir="$3"
  local sku_keys flags=""

  sku_keys=$(echo "$entry_json" | python3 -c "
import json, sys
entry = json.load(sys.stdin)
skus = entry.get('skus', {})
print(' '.join(skus.keys()))
" 2>/dev/null || echo "")

  local wf_path="$repo_dir/.github/workflows/$(basename "$wf_file")"

  # Check what dispatch inputs the workflow supports
  local has_blackhole=false has_wormhole=false
  if [ -f "$wf_path" ]; then
    grep -q 'blackhole:' "$wf_path" 2>/dev/null && has_blackhole=true
    grep -q 'wormhole:' "$wf_path" 2>/dev/null && has_wormhole=true
  fi

  if $has_blackhole || $has_wormhole; then
    local need_bh=false need_wh=false
    for sku in $sku_keys; do
      case "$sku" in
        bh_*) need_bh=true ;;
        wh_*) need_wh=true ;;
      esac
    done

    if $has_blackhole; then
      if $need_bh; then flags="$flags -f blackhole=true"; else flags="$flags -f blackhole=false"; fi
    fi
    if $has_wormhole; then
      if $need_wh; then flags="$flags -f wormhole=true"; else flags="$flags -f wormhole=false"; fi
    fi
  fi

  echo "$flags"
}

# ---------------------------------------------------------------------------
# build_pruned_yaml TEST_JOB TEST_NAME TEST_ENTRY_JSON
#
# Generates a single-entry YAML list for the pruned test matrix.
# Outputs YAML content on stdout.
# ---------------------------------------------------------------------------
build_pruned_yaml() {
  local test_job="$1" test_name="$2" entry_json="$3"

  python3 -c "
import json, sys, yaml

entry = json.loads('''$entry_json''')
test_name = '''$test_name'''

# Build the pytest command — quote if parametrized (contains brackets)
if '[' in test_name:
    cmd = 'pytest \"' + test_name + '\"'
else:
    cmd = 'pytest ' + test_name

pruned = {
    'name': entry.get('name', '$test_job'),
    'cmd': cmd,
    'skus': entry.get('skus', {}),
    'owner_id': entry.get('owner_id', 'UNKNOWN'),
    'team': entry.get('team', 'UNKNOWN'),
}

# Add comment
print('# Pruned to single test for bug escape verification')
print(yaml.dump([pruned], default_flow_style=False).rstrip())
" 2>/dev/null
}

# ---------------------------------------------------------------------------
# poll_run_completion RUN_ID INTERVAL_SECONDS MAX_WAIT_MINUTES
#
# Polls a GitHub Actions run until it completes or times out.
# Returns the conclusion on stdout.
# ---------------------------------------------------------------------------
poll_run_completion() {
  local run_id="$1" interval="${2:-120}" max_wait="${3:-120}"
  local deadline=$((SECONDS + max_wait * 60))
  local status conclusion

  # Progress messages go to stderr so they don't get captured by $(...)
  printf '[verify] Polling run %s (interval=%ss, max_wait=%sm)\n' "$run_id" "$interval" "$max_wait" >&2

  while [ "$SECONDS" -lt "$deadline" ]; do
    status=$(gh run view "$run_id" --json status --jq '.status' 2>/dev/null || echo "unknown")
    if [ "$status" = "completed" ]; then
      conclusion=$(gh run view "$run_id" --json conclusion --jq '.conclusion' 2>/dev/null || echo "unknown")
      printf '[verify] Run %s completed: %s\n' "$run_id" "$conclusion" >&2
      echo "$conclusion"
      return 0
    fi
    printf '[verify]   Run %s: status=%s (%dm remaining)\n' "$run_id" "$status" "$(( (deadline - SECONDS) / 60 ))" >&2
    sleep "$interval"
  done

  printf '[verify][WARN] Run %s timed out after %sm\n' "$run_id" "$max_wait" >&2
  echo "timed_out"
  return 1
}

# ---------------------------------------------------------------------------
# check_no_tests_ran RUN_ID TEST_JOB
#
# After a run completes, check if the specific job hit "no tests ran".
# Returns 0 (true) if no tests ran, 1 otherwise.
# ---------------------------------------------------------------------------
check_no_tests_ran() {
  local run_id="$1" test_job="$2"

  if gh run view "$run_id" --log 2>/dev/null \
       | grep -F "$test_job" \
       | grep -qE "no tests ran|collected 0 items"; then
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------------------
# check_failure_is_real RUN_ID TEST_JOB EXPECTED_FAILURE_SIG CURSOR_API_KEY
#
# Uses the Cursor AI API to classify a job failure as real vs infra/unrelated.
# Fetches the last 200 lines of the job log and asks Cursor to analyze them.
#
# Returns one of: real_failure, infra_failure, unrelated_failure, inconclusive
# on stdout.
#
# Conservative defaults:
#   - If CURSOR_API_KEY is empty → returns "real_failure" (skip check)
#   - If log fetch fails → returns "real_failure" (don't drop real failures)
#   - If API call fails or response unparseable → returns "inconclusive"
# ---------------------------------------------------------------------------
check_failure_is_real() {
  local run_id="$1" test_job="$2" expected_sig="${3:-}" cursor_api_key="${4:-}"

  # Skip if no API key — conservative: treat as real
  if [ -z "$cursor_api_key" ]; then
    verify_info "check_failure_is_real: no CURSOR_API_KEY, assuming real_failure"
    echo "real_failure"
    return 0
  fi

  # Fetch job logs via GitHub REST API (no gh CLI dependency)
  local log_tail job_id
  job_id=$(curl -s -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
    "https://api.github.com/repos/${OWNER_REPO}/actions/runs/${run_id}/jobs" 2>/dev/null \
    | python3 -c "
import sys,json
d=json.load(sys.stdin)
test_job_name='${test_job}'
for j in d.get('jobs',[]):
    if test_job_name in j.get('name',''):
        print(j['id'])
        break
" 2>/dev/null) || true

  if [ -n "$job_id" ]; then
    log_tail=$(curl -s -L -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
      "https://api.github.com/repos/${OWNER_REPO}/actions/jobs/${job_id}/logs" 2>/dev/null \
      | tail -200) || true
  fi

  if [ -z "$log_tail" ]; then
    verify_warn "check_failure_is_real: could not fetch logs for run $run_id job '$test_job', assuming real_failure"
    echo "real_failure"
    return 0
  fi

  # Escape the log content for JSON embedding
  local escaped_logs
  escaped_logs=$(printf '%s' "$log_tail" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

  local prompt
  prompt=$(cat <<PROMPT_EOF
You are analyzing a CI job log to determine if a test failure is a real test failure or an infrastructure/unrelated failure.

**Job name:** ${test_job}
**Expected failure signature:** ${expected_sig}

**Last 200 lines of job log:**
${log_tail}

Classify this failure into exactly one category:
- "real_failure": The test actually ran and failed due to a test assertion, timeout in test code, or a product bug. The failure is related to the expected failure signature.
- "infra_failure": The failure is caused by CI infrastructure problems — examples: HTTP 503 proxy errors, git clone/submodule failures, Docker pull failures, runner setup errors, pip install failures, network timeouts, disk space issues.
- "unrelated_failure": The test job failed but for a reason completely unrelated to the expected failure signature — a different test crashed, an import error in unrelated code, a segfault in a different component.

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON):
{"classification": "real_failure" | "infra_failure" | "unrelated_failure", "reason": "brief one-line explanation"}
PROMPT_EOF
)

  # Build the JSON payload for the Cursor API (OpenAI-compatible endpoint)
  local payload
  payload=$(python3 -c "
import json, sys
prompt = sys.stdin.read()
print(json.dumps({
    'model': 'claude-3-5-sonnet',
    'messages': [{'role': 'user', 'content': prompt}],
    'max_tokens': 256
}))
" <<< "$prompt")

  local response http_code
  local tmpfile
  tmpfile=$(mktemp)

  http_code=$(curl -s -o "$tmpfile" -w "%{http_code}" \
    -X POST "https://api.cursor.sh/v1/chat/completions" \
    -H "Authorization: Bearer $cursor_api_key" \
    -H "Content-Type: application/json" \
    -d "$payload" 2>/dev/null) || true

  if [ "$http_code" != "200" ]; then
    verify_warn "check_failure_is_real: Cursor API returned HTTP $http_code, returning inconclusive"
    rm -f "$tmpfile"
    echo "inconclusive"
    return 0
  fi

  # Extract the classification from the response
  local classification
  classification=$(python3 -c "
import json, sys, re
try:
    resp = json.load(sys.stdin)
    content = resp['choices'][0]['message']['content']
    # Try to parse as JSON directly
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Strip markdown fences if present
        cleaned = re.sub(r'^\`\`\`(?:json)?\s*', '', content.strip())
        cleaned = re.sub(r'\s*\`\`\`$', '', cleaned)
        result = json.loads(cleaned)
    c = result.get('classification', '')
    reason = result.get('reason', 'no reason given')
    if c in ('real_failure', 'infra_failure', 'unrelated_failure'):
        print(f'{c}|{reason}', end='')
    else:
        print('inconclusive|unexpected classification: ' + c, end='')
except Exception as e:
    print('inconclusive|parse error: ' + str(e), end='')
" < "$tmpfile") || true

  rm -f "$tmpfile"

  if [ -z "$classification" ]; then
    verify_warn "check_failure_is_real: empty response from parser, returning inconclusive"
    echo "inconclusive"
    return 0
  fi

  local class_value class_reason
  class_value="${classification%%|*}"
  class_reason="${classification#*|}"

  verify_info "check_failure_is_real: run=$run_id classification=$class_value reason=$class_reason"
  echo "$class_value"
  return 0
}

# ---------------------------------------------------------------------------
# wait_for_run_to_appear BRANCH WORKFLOW_FILE MAX_ATTEMPTS
#
# After dispatching, the run takes a few seconds to appear. Poll until we
# find a queued/in_progress run on the branch, or give up.
# Returns the run ID on stdout.
# ---------------------------------------------------------------------------
wait_for_run_to_appear() {
  local branch="$1" wf_file="$2" max_attempts="${3:-15}"
  local wf_basename run_id attempt

  wf_basename=$(basename "$wf_file")

  for attempt in $(seq 1 "$max_attempts"); do
    run_id=$(gh run list --workflow="$wf_basename" --branch="$branch" \
      --limit=1 --json databaseId,status \
      --jq '.[] | select(.status == "queued" or .status == "in_progress" or .status == "completed") | .databaseId' \
      2>/dev/null || echo "")

    if [ -n "$run_id" ]; then
      echo "$run_id"
      return 0
    fi
    printf '[verify]   Waiting for run to appear on %s (attempt %d/%d)\n' "$branch" "$attempt" "$max_attempts" >&2
    sleep 5
  done

  printf '[verify][WARN] Could not find run for %s on %s after %d attempts\n' "$wf_basename" "$branch" "$max_attempts" >&2
  return 1
}

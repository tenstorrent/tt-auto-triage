#!/usr/bin/env bash
set -euo pipefail

# Phase 3: Find Fix Points
#
# For each confirmed consistent failure:
#   1. Walk forward through subsequent runs of the same workflow/job
#   2. Find the first run where the job passed (or failure signature changed)
#   3. Skip failures still happening now
#   4. For fixed failures, list commits between the transition pair
#   5. Invoke Cursor agent to attribute the fix to specific commit(s)
#   6. Write fix-points.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
FAILURES_INPUT="$OUTPUT_DIR/consistent-failures.json"
FIX_POINTS_OUTPUT="$OUTPUT_DIR/fix-points.json"
PROMPT_TEMPLATE="$SCRIPT_DIR/prompts/analyze_fix_commit.txt"

# Path to the existing auto-triage tools
AT_TOOLS_DIR="$SCRIPT_DIR/../.github/actions/auto-triage/auto_triage/tools"

MAX_FORWARD_RUNS=50

# Initialize output
echo '[]' > "$FIX_POINTS_OUTPUT"

num_failures=$(jq 'length' "$FAILURES_INPUT")
log_info "Phase 3: analyzing $num_failures consistent failures for fix points"

for i in $(seq 0 $((num_failures - 1))); do
  entry=$(jq -c ".[$i]" "$FAILURES_INPUT")
  wf_path=$(echo "$entry" | jq -r '.workflow')
  job_name=$(echo "$entry" | jq -r '.job')
  test_name=$(echo "$entry" | jq -r '.test_name')
  failure_sig=$(echo "$entry" | jq -r '.failure_signature')
  test_layer=$(echo "$entry" | jq -r '.test_layer')
  failing_run_ids=$(echo "$entry" | jq -c '.failing_run_ids')
  last_failing_run_id=$(echo "$failing_run_ids" | jq '.[- 1]')

  log_info "  [$((i+1))/$num_failures] $job_name — looking for fix after run $last_failing_run_id"

  # Get the workflow ID
  wf_basename=$(basename "$wf_path")
  wf_id=$(get_workflow_id "$wf_basename" 2>/dev/null || echo "")
  if [ -z "$wf_id" ]; then
    log_warn "    Could not resolve workflow ID — skipping"
    continue
  fi

  # Fetch runs newer than the last failing run
  # We page through runs (newest-first) and collect those created after our failure
  last_fail_date=$(get_run_info "$last_failing_run_id" | jq -r '.created_at // empty' 2>/dev/null || echo "")
  if [ -z "$last_fail_date" ]; then
    log_warn "    Could not get date for run $last_failing_run_id — skipping"
    continue
  fi

  # Collect runs created after the last failing run
  subsequent_runs="[]"
  page=1
  found_our_run=false
  while [ "$page" -le 5 ] && [ "$found_our_run" = "false" ]; do
    page_json=$(get_workflow_runs "$wf_id" "$page")
    runs_on_page=$(echo "$page_json" | jq '.workflow_runs | length' 2>/dev/null || echo 0)
    if [ "$runs_on_page" -eq 0 ]; then
      break
    fi

    # Filter runs that are newer than our last failing run
    for r in $(seq 0 $((runs_on_page - 1))); do
      rid=$(echo "$page_json" | jq -r ".workflow_runs[$r].id")
      rdate=$(echo "$page_json" | jq -r ".workflow_runs[$r].created_at")

      if [ "$rid" = "$last_failing_run_id" ]; then
        found_our_run=true
        break
      fi

      subsequent_runs=$(echo "$subsequent_runs" | jq --argjson run "$(echo "$page_json" | jq ".workflow_runs[$r]")" '. += [$run]')
    done

    page=$((page + 1))
  done

  # Reverse to chronological order (oldest first after the failure)
  subsequent_runs=$(echo "$subsequent_runs" | jq 'reverse')
  num_subsequent=$(echo "$subsequent_runs" | jq 'length')

  if [ "$num_subsequent" -eq 0 ]; then
    log_info "    No subsequent runs found — skipping (failure may still be active)"
    continue
  fi

  log_info "    Found $num_subsequent subsequent runs to check"

  # Walk forward to find the first run where the job passed
  first_passing_run_id=""
  first_passing_run_sha=""
  last_failing_run_sha=""

  # Get the SHA for the last failing run
  last_failing_run_sha=$(get_run_info "$last_failing_run_id" | jq -r '.head_sha // empty' 2>/dev/null || echo "")

  for r in $(seq 0 $((num_subsequent - 1))); do
    if [ "$r" -ge "$MAX_FORWARD_RUNS" ]; then
      log_info "    Reached max forward scan ($MAX_FORWARD_RUNS) without finding a pass — skipping"
      break
    fi

    run_id=$(echo "$subsequent_runs" | jq -r ".[$r].id")
    run_sha=$(echo "$subsequent_runs" | jq -r ".[$r].head_sha")

    # Check this specific job's conclusion in this run (fuzzy match on job name)
    jobs_json=$(get_jobs_for_run "$run_id")
    job_conclusion=$(echo "$jobs_json" | jq -r --arg jn "$job_name" '
      .jobs[] | select(.name == $jn or (.name | endswith(" / " + $jn)) or (.name | contains($jn))) | .conclusion // "unknown"
    ' 2>/dev/null | head -1)

    if [ -z "$job_conclusion" ]; then
      log_info "      Run $run_id: job '$job_name' not present — skipping (gap)"
      continue
    fi

    if [ "$job_conclusion" = "success" ]; then
      first_passing_run_id="$run_id"
      first_passing_run_sha="$run_sha"
      log_info "    Found fix transition: run $last_failing_run_id (fail) -> run $run_id (pass)"
      break
    fi
  done

  if [ -z "$first_passing_run_id" ]; then
    log_info "    No passing run found — failure is still active, skipping"
    continue
  fi

  if [ -z "$last_failing_run_sha" ] || [ -z "$first_passing_run_sha" ]; then
    log_warn "    Missing SHAs for transition pair — skipping"
    continue
  fi

  # List commits between the two SHAs
  commits_file="$(mktemp)"
  if [ -x "$AT_TOOLS_DIR/list_commits_between.sh" ]; then
    bash "$AT_TOOLS_DIR/list_commits_between.sh" "$last_failing_run_sha" "$first_passing_run_sha" "$commits_file" 2>/dev/null || {
      log_warn "    list_commits_between.sh failed, falling back to gh api"
      gh api "repos/${AT_OWNER_REPO}/compare/${last_failing_run_sha}...${first_passing_run_sha}" \
        --jq '.commits | map({"sha": .sha, "short": .sha[:8], "subject": .commit.message | split("\n")[0]})' \
        > "$commits_file" 2>/dev/null || echo "[]" > "$commits_file"
    }
  else
    gh api "repos/${AT_OWNER_REPO}/compare/${last_failing_run_sha}...${first_passing_run_sha}" \
      --jq '.commits | map({"sha": .sha, "short": .sha[:8], "subject": .commit.message | split("\n")[0]})' \
      > "$commits_file" 2>/dev/null || echo "[]" > "$commits_file"
  fi

  num_commits=$(jq 'length' "$commits_file" 2>/dev/null || echo 0)
  log_info "    $num_commits commits between transition pair"

  if [ "$num_commits" -eq 0 ]; then
    log_warn "    No commits found between SHAs — skipping"
    rm -f "$commits_file"
    continue
  fi

  # For each commit, get changed files and ask the agent if it's the fix
  candidate_fixes="[]"

  for c in $(seq 0 $((num_commits - 1))); do
    commit_sha=$(jq -r ".[$c].sha" "$commits_file")
    commit_subject=$(jq -r ".[$c].subject" "$commits_file")

    # Get changed files
    files_json=""
    if [ -x "$AT_TOOLS_DIR/get_changed_files.sh" ]; then
      changed_files_output="$(mktemp)"
      bash "$AT_TOOLS_DIR/get_changed_files.sh" "$commit_sha" "$changed_files_output" 2>/dev/null || true
      if [ -f "$changed_files_output" ] && [ -s "$changed_files_output" ]; then
        files_json=$(jq -r '[.[].file]' "$changed_files_output" 2>/dev/null || echo "[]")
      fi
      rm -f "$changed_files_output"
    fi

    if [ -z "$files_json" ] || [ "$files_json" = "[]" ]; then
      files_json=$(gh api "repos/${AT_OWNER_REPO}/commits/${commit_sha}" \
        --jq '[.files[].filename]' 2>/dev/null || echo "[]")
    fi
    if ! echo "$files_json" | jq empty 2>/dev/null; then
      files_json="[]"
    fi

    files_list=$(echo "$files_json" | jq -r '.[]' 2>/dev/null | head -50 || echo "")

    # Fetch PR context for this commit
    pr_number=""
    pr_title=""
    pr_body=""
    pr_json=$(gh api "repos/${AT_OWNER_REPO}/commits/${commit_sha}/pulls" \
      --jq '.[0] // empty' 2>/dev/null || echo "")
    if [ -n "$pr_json" ]; then
      pr_number=$(echo "$pr_json" | jq -r '.number // ""' 2>/dev/null || echo "")
      pr_title=$(echo "$pr_json" | jq -r '.title // ""' 2>/dev/null || echo "")
      pr_body=$(echo "$pr_json" | jq -r '.body // ""' 2>/dev/null || echo "")
      # Truncate PR body to avoid prompt size issues
      pr_body="${pr_body:0:2000}"
    fi

    # Ask the Cursor agent to analyze this commit
    agent_output="$(mktemp)"
    if cursor_agent_from_template "$PROMPT_TEMPLATE" "$agent_output" \
         "TEST_NAME=$test_name" \
         "FAILURE_SIGNATURE=$failure_sig" \
         "WORKFLOW_PATH=$wf_path" \
         "TEST_LAYER=$test_layer" \
         "COMMIT_SHA=$commit_sha" \
         "COMMIT_MESSAGE=$commit_subject" \
         "COMMIT_FILES=$files_list" \
         "PR_NUMBER=$pr_number" \
         "PR_TITLE=$pr_title" \
         "PR_BODY=$pr_body"; then

      is_likely_fix=$(jq -r 'if .is_likely_fix == null then false else .is_likely_fix end' "$agent_output" 2>/dev/null || echo "false")
      fix_confidence=$(jq -r '.fix_confidence // "low"' "$agent_output" 2>/dev/null || echo "low")
      fix_layer=$(jq -r '.fix_layer // "unknown"' "$agent_output" 2>/dev/null || echo "unknown")
      fix_reasoning=$(jq -r '.reasoning // ""' "$agent_output" 2>/dev/null || echo "")

      if [ "$is_likely_fix" = "true" ]; then
        log_info "      Commit $commit_sha: LIKELY FIX (layer=$fix_layer, confidence=$fix_confidence)"

        candidate_fixes=$(echo "$candidate_fixes" | jq \
          --arg sha "$commit_sha" \
          --arg msg "$commit_subject" \
          --argjson files "$files_json" \
          --arg layer "$fix_layer" \
          --arg conf "$fix_confidence" \
          --arg reason "$fix_reasoning" \
          '. += [{"sha": $sha, "message": $msg, "files_changed": $files, "fix_layer": $layer, "confidence": $conf, "reasoning": $reason}]')
      fi
    else
      log_warn "      Agent call failed for commit $commit_sha"
    fi

    rm -f "$agent_output"
  done

  num_fixes=$(echo "$candidate_fixes" | jq 'length')

  if [ "$num_fixes" -eq 0 ]; then
    log_info "    No specific fix commit identified by agent — using file-based heuristic"

    all_files="[]"
    for c in $(seq 0 $((num_commits - 1))); do
      commit_sha=$(jq -r ".[$c].sha" "$commits_file")
      commit_files=$(gh api "repos/${AT_OWNER_REPO}/commits/${commit_sha}" \
        --jq '[.files[].filename]' 2>/dev/null || echo "[]")
      if ! echo "$commit_files" | jq empty 2>/dev/null; then
        commit_files="[]"
      fi
      # Ensure commit_files is a JSON array (not an object or string)
      if [ "$(echo "$commit_files" | jq 'type' 2>/dev/null)" != '"array"' ]; then
        commit_files="[]"
      fi
      all_files=$(echo "$all_files" "$commit_files" | jq -s '.[0] + .[1]')
    done

    # Validate all_files is a flat array of strings before processing
    if ! echo "$all_files" | jq 'if type == "array" and all(type == "string") then true else false end' 2>/dev/null | grep -q true; then
      log_warn "    Heuristic: all_files is not a valid string array — defaulting to unknown layer"
      all_files="[]"
    fi

    dominant_layer=$(be_dominant_layer "$all_files" 2>/dev/null || echo "unknown")

    candidate_fixes=$(jq -n \
      --arg sha "$(jq -r '.[-1].sha' "$commits_file")" \
      --arg msg "$(jq -r '.[-1].subject' "$commits_file")" \
      --argjson files "$all_files" \
      --arg layer "$dominant_layer" \
      '[{"sha": $sha, "message": $msg, "files_changed": $files, "fix_layer": $layer, "confidence": "low", "reasoning": "Heuristic: no specific fix commit identified by agent; using dominant layer of all commits in transition window"}]')
  fi

  # Add to fix-points output
  jq --argjson failure "$entry" \
     --arg last_fail_id "$last_failing_run_id" \
     --arg first_pass_id "$first_passing_run_id" \
     --arg last_fail_sha "$last_failing_run_sha" \
     --arg first_pass_sha "$first_passing_run_sha" \
     --argjson fixes "$candidate_fixes" \
     '. += [{
       "failure": $failure,
       "last_failing_run_id": ($last_fail_id | tonumber),
       "first_passing_run_id": ($first_pass_id | tonumber),
       "last_failing_sha": $last_fail_sha,
       "first_passing_sha": $first_pass_sha,
       "candidate_fix_commits": $fixes
     }]' \
     "$FIX_POINTS_OUTPUT" > "${FIX_POINTS_OUTPUT}.tmp" && mv "${FIX_POINTS_OUTPUT}.tmp" "$FIX_POINTS_OUTPUT"

  rm -f "$commits_file"
done

total_fixpoints=$(jq 'length' "$FIX_POINTS_OUTPUT")
log_info "Phase 3 done: $total_fixpoints fix points identified"

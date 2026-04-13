#!/usr/bin/env bash
set -euo pipefail

# Phase 3: Find Fix Points
#
# For each confirmed consistent failure:
#   1. Skip if marked likely_flaky by Phase 2
#   2. Walk forward through subsequent runs of the same workflow/job
#   3. Find the first run where the job passed
#   4. Use gh api compare to get commits between SHAs (bash, fast)
#   5. Pass compact commit list to agent for analysis (no tool calls needed)
#   6. Write fix-points.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
FAILURES_INPUT="$OUTPUT_DIR/consistent-failures.json"
FIX_POINTS_OUTPUT="$OUTPUT_DIR/fix-points.json"
PROMPT_TEMPLATE="$SCRIPT_DIR/prompts/find_fix_commit.txt"

MAX_FORWARD_RUNS=50
MAX_COMMITS_PER_WINDOW=50

SAVED_RETRIES="${CURSOR_AGENT_MAX_RETRIES:-2}"
SAVED_TIMEOUT="${CURSOR_AGENT_TIMEOUT:-300}"
export CURSOR_AGENT_MAX_RETRIES=1
export CURSOR_AGENT_TIMEOUT=120

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
  last_failing_run_id=$(echo "$failing_run_ids" | jq '.[-1]')
  likely_flaky=$(echo "$entry" | jq -r '.likely_flaky // false')

  log_info "  [$((i+1))/$num_failures] $job_name — looking for fix after run $last_failing_run_id"

  if [ "$likely_flaky" = "true" ]; then
    log_warn "    Marked as likely_flaky — skipping (no agent call)"
    jq --argjson failure "$entry" \
       '. += [{"failure": $failure, "skipped_reason": "likely_flaky"}]' \
       "$FIX_POINTS_OUTPUT" > "${FIX_POINTS_OUTPUT}.tmp" && mv "${FIX_POINTS_OUTPUT}.tmp" "$FIX_POINTS_OUTPUT"
    continue
  fi

  wf_basename=$(basename "$wf_path")
  wf_id=$(cached_get_workflow_id "$wf_basename")
  if [ -z "$wf_id" ]; then
    log_warn "    Could not resolve workflow ID — skipping"
    continue
  fi

  last_fail_date=$(get_run_info "$last_failing_run_id" | jq -r '.created_at // empty' 2>/dev/null || echo "")
  if [ -z "$last_fail_date" ]; then
    log_warn "    Could not get date for run $last_failing_run_id — skipping"
    continue
  fi

  subsequent_runs="[]"
  page=1
  found_our_run=false
  while [ "$page" -le 5 ] && [ "$found_our_run" = "false" ]; do
    page_json=$(get_workflow_runs "$wf_id" "$page")
    runs_on_page=$(echo "$page_json" | jq '.workflow_runs | length' 2>/dev/null || echo 0)
    if [ "$runs_on_page" -eq 0 ]; then
      break
    fi

    for r in $(seq 0 $((runs_on_page - 1))); do
      rid=$(echo "$page_json" | jq -r ".workflow_runs[$r].id")
      if [ "$rid" = "$last_failing_run_id" ]; then
        found_our_run=true
        break
      fi
      subsequent_runs=$(echo "$subsequent_runs" | jq --argjson run "$(echo "$page_json" | jq ".workflow_runs[$r]")" '. += [$run]')
    done

    page=$((page + 1))
  done

  subsequent_runs=$(echo "$subsequent_runs" | jq 'reverse')
  num_subsequent=$(echo "$subsequent_runs" | jq 'length')

  if [ "$num_subsequent" -eq 0 ]; then
    log_info "    No subsequent runs found — skipping (failure may still be active)"
    continue
  fi

  log_info "    Found $num_subsequent subsequent runs to check"

  first_passing_run_id=""
  first_passing_run_sha=""
  last_failing_run_sha=$(get_run_info "$last_failing_run_id" | jq -r '.head_sha // empty' 2>/dev/null || echo "")

  for r in $(seq 0 $((num_subsequent - 1))); do
    if [ "$r" -ge "$MAX_FORWARD_RUNS" ]; then
      log_info "    Reached max forward scan ($MAX_FORWARD_RUNS) without finding a pass — skipping"
      break
    fi

    run_id=$(echo "$subsequent_runs" | jq -r ".[$r].id")
    run_sha=$(echo "$subsequent_runs" | jq -r ".[$r].head_sha")

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

  log_info "    Transition: $last_failing_run_sha -> $first_passing_run_sha"

  # ---- Get commits via gh api compare (bash, fast) ----
  commits_json=$(gh api "repos/${AT_OWNER_REPO}/compare/${last_failing_run_sha}...${first_passing_run_sha}" \
    --jq '[.commits[] | {sha: .sha, msg: (.commit.message | split("\n")[0])}]' 2>/dev/null || echo "[]")

  num_commits=$(echo "$commits_json" | jq 'length' 2>/dev/null || echo 0)
  log_info "    $num_commits commits between transition pair"

  if [ "$num_commits" -eq 0 ]; then
    log_warn "    No commits found between SHAs — skipping"
    continue
  fi

  if [ "$num_commits" -gt "$MAX_COMMITS_PER_WINDOW" ]; then
    log_warn "    Transition window too wide ($num_commits commits > $MAX_COMMITS_PER_WINDOW) — skipping"
    continue
  fi

  # Build compact list for agent prompt
  commits_list=""
  for c in $(seq 0 $((num_commits - 1))); do
    sha=$(echo "$commits_json" | jq -r ".[$c].sha")
    msg=$(echo "$commits_json" | jq -r ".[$c].msg")
    commits_list="${commits_list}${sha} ${msg}
"
  done

  # ---- Agent call: pure analysis, no tool calls needed ----
  candidate_fixes="[]"

  agent_output="$(mktemp)"
  if cursor_agent_from_template "$PROMPT_TEMPLATE" "$agent_output" \
       "TEST_NAME=$test_name" \
       "FAILURE_SIGNATURE=$failure_sig" \
       "WORKFLOW_PATH=$wf_path" \
       "TEST_LAYER=$test_layer" \
       "NUM_COMMITS=$num_commits" \
       "COMMITS_LIST=$commits_list"; then

    sha=$(jq -r '.sha // "null"' "$agent_output" 2>/dev/null || echo "null")
    is_fix=$(jq -r '.is_likely_fix // false' "$agent_output" 2>/dev/null || echo "false")
    is_skip=$(jq -r '.is_skip_or_disable // false' "$agent_output" 2>/dev/null || echo "false")
    confidence=$(jq -r '.fix_confidence // "low"' "$agent_output" 2>/dev/null || echo "low")
    layer=$(jq -r '.fix_layer // "unknown"' "$agent_output" 2>/dev/null || echo "unknown")
    reasoning=$(jq -r '.reasoning // ""' "$agent_output" 2>/dev/null || echo "")

    if [ "$is_fix" = "true" ] && [ "$sha" != "null" ] && [ -n "$sha" ]; then
      commit_msg=$(echo "$commits_json" | jq -r --arg s "$sha" '.[] | select(.sha == $s) | .msg // ""')

      # Determine fix layer from actual file paths (authoritative, overrides agent guess)
      fix_files_json=$(gh api "repos/${AT_OWNER_REPO}/commits/${sha}" \
        --jq '[.files[].filename]' 2>/dev/null || echo "[]")
      file_layer=$(be_dominant_layer "$fix_files_json" 2>/dev/null || echo "unknown")
      if [ "$file_layer" != "unknown" ] && [ -n "$file_layer" ]; then
        layer="$file_layer"
      fi

      # Fetch PR metadata for this commit
      pr_number=""
      pr_url=""
      pr_title=""
      pr_json=$(gh api "repos/${AT_OWNER_REPO}/commits/${sha}/pulls" \
        --jq '.[0] | {number, html_url, title}' 2>/dev/null || echo "{}")
      if [ -n "$pr_json" ] && [ "$pr_json" != "{}" ]; then
        pr_number=$(echo "$pr_json" | jq -r '.number // empty' 2>/dev/null || echo "")
        pr_url=$(echo "$pr_json" | jq -r '.html_url // empty' 2>/dev/null || echo "")
        pr_title=$(echo "$pr_json" | jq -r '.title // empty' 2>/dev/null || echo "")
      fi

      log_info "      Agent identified fix: ${sha:0:12} (layer=$layer, confidence=$confidence)"
      log_info "      Message: ${commit_msg:0:120}"
      candidate_fixes=$(jq -n \
        --arg sha "$sha" \
        --arg msg "$commit_msg" \
        --argjson files "$fix_files_json" \
        --arg layer "$layer" \
        --arg conf "$confidence" \
        --arg reason "$reasoning" \
        --arg is_skip "$is_skip" \
        --arg pr_num "$pr_number" \
        --arg pr_url "$pr_url" \
        --arg pr_title "$pr_title" \
        '[{"sha": $sha, "message": $msg, "files_changed": $files, "fix_layer": $layer, "confidence": $conf, "reasoning": $reason, "is_skip_or_disable": ($is_skip == "true"), "pr_number": (if $pr_num == "" then null else ($pr_num | tonumber) end), "pr_url": (if $pr_url == "" then null else $pr_url end), "pr_title": (if $pr_title == "" then null else $pr_title end)}]')
    else
      log_info "    Agent could not identify a specific fix commit"
      if [ -n "$reasoning" ] && [ "$reasoning" != "" ] && [ "$reasoning" != "null" ]; then
        log_info "      Reasoning: ${reasoning:0:200}"
      fi
    fi
  else
    log_warn "    Agent call failed for fix attribution"
  fi
  rm -f "$agent_output"

  num_fixes=$(echo "$candidate_fixes" | jq 'length')
  if [ "$num_fixes" -eq 0 ]; then
    # Try to determine layer from the first passing SHA's changed files
    fallback_files=$(gh api "repos/${AT_OWNER_REPO}/commits/${first_passing_run_sha}" \
      --jq '[.files[].filename]' 2>/dev/null || echo "[]")
    fallback_layer=$(be_dominant_layer "$fallback_files" 2>/dev/null || echo "unknown")
    candidate_fixes=$(jq -n \
      --arg sha "$first_passing_run_sha" \
      --argjson files "$fallback_files" \
      --arg layer "$fallback_layer" \
      '[{"sha": $sha, "message": "", "files_changed": $files, "fix_layer": $layer, "confidence": "low", "reasoning": "Agent could not identify specific fix; using first passing run SHA"}]')
  fi

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
done

export CURSOR_AGENT_MAX_RETRIES="$SAVED_RETRIES"
export CURSOR_AGENT_TIMEOUT="$SAVED_TIMEOUT"

total_fixpoints=$(jq 'length' "$FIX_POINTS_OUTPUT")
log_info "Phase 3 done: $total_fixpoints fix points identified"

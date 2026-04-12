#!/usr/bin/env bash
set -euo pipefail

# Phase 3: Find Fix Points
#
# For each confirmed consistent failure:
#   1. Skip if marked likely_flaky by Phase 2
#   2. Walk forward through subsequent runs of the same workflow/job
#   3. Find the first run where the job passed
#   4. List commits between the transition pair (cap at 15)
#   5. Gather all commit metadata in parallel, then batch into ONE agent call
#   6. Write fix-points.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
FAILURES_INPUT="$OUTPUT_DIR/consistent-failures.json"
FIX_POINTS_OUTPUT="$OUTPUT_DIR/fix-points.json"
BATCH_PROMPT_TEMPLATE="$SCRIPT_DIR/prompts/batch_analyze_fix_commits.txt"

AT_TOOLS_DIR="$SCRIPT_DIR/../.github/actions/auto-triage/auto_triage/tools"

MAX_FORWARD_RUNS=50
MAX_COMMITS_PER_WINDOW=15

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

  # Skip flaky failures to save agent calls
  if [ "$likely_flaky" = "true" ]; then
    log_warn "    Marked as likely_flaky — skipping (no agent call)"
    jq --argjson failure "$entry" \
       '. += [{"failure": $failure, "skipped_reason": "likely_flaky"}]' \
       "$FIX_POINTS_OUTPUT" > "${FIX_POINTS_OUTPUT}.tmp" && mv "${FIX_POINTS_OUTPUT}.tmp" "$FIX_POINTS_OUTPUT"
    continue
  fi

  # Get the workflow ID
  wf_basename=$(basename "$wf_path")
  wf_id=$(cached_get_workflow_id "$wf_basename")
  if [ -z "$wf_id" ]; then
    log_warn "    Could not resolve workflow ID — skipping"
    continue
  fi

  # Fetch runs newer than the last failing run
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

  # Walk forward to find the first passing run (fuzzy job matching)
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

  if [ "$num_commits" -gt "$MAX_COMMITS_PER_WINDOW" ]; then
    log_warn "    Transition window too wide ($num_commits commits > $MAX_COMMITS_PER_WINDOW) — skipping (unreliable attribution)"
    rm -f "$commits_file"
    continue
  fi

  # ---- Gather commit metadata in parallel ----
  commit_meta_dir="$(mktemp -d)"

  for c in $(seq 0 $((num_commits - 1))); do
    commit_sha=$(jq -r ".[$c].sha" "$commits_file")
    commit_subject=$(jq -r ".[$c].subject" "$commits_file")

    (
      meta_file="$commit_meta_dir/commit_${c}.json"

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

      # Get PR context
      pr_number="" pr_title="" pr_body=""
      pr_json=$(gh api "repos/${AT_OWNER_REPO}/commits/${commit_sha}/pulls" \
        --jq '.[0] // empty' 2>/dev/null || echo "")
      if [ -n "$pr_json" ]; then
        pr_number=$(echo "$pr_json" | jq -r '.number // ""')
        pr_title=$(echo "$pr_json" | jq -r '.title // ""')
        pr_body=$(echo "$pr_json" | jq -r '.body // ""')
        pr_body="${pr_body:0:1500}"
      fi

      jq -n \
        --argjson idx "$c" \
        --arg sha "$commit_sha" \
        --arg subject "$commit_subject" \
        --argjson files "$files_json" \
        --arg pr_num "$pr_number" \
        --arg pr_title "$pr_title" \
        --arg pr_body "$pr_body" \
        '{idx: $idx, sha: $sha, subject: $subject, files: $files, pr_number: $pr_num, pr_title: $pr_title, pr_body: $pr_body}' \
        > "$meta_file"
    ) &

    # Cap at 8 concurrent
    if (( (c + 1) % 8 == 0 )); then
      wait
    fi
  done
  wait

  # ---- Build the batch prompt ----
  commits_list=""
  all_commit_files="[]"

  for c in $(seq 0 $((num_commits - 1))); do
    meta_file="$commit_meta_dir/commit_${c}.json"
    if [ ! -f "$meta_file" ]; then
      log_warn "    Missing metadata for commit index $c — skipping"
      continue
    fi

    sha=$(jq -r '.sha' "$meta_file")
    subject=$(jq -r '.subject' "$meta_file")
    files_list=$(jq -r '.files[]' "$meta_file" 2>/dev/null | head -30 || echo "")
    pr_num=$(jq -r '.pr_number' "$meta_file")
    pr_title=$(jq -r '.pr_title' "$meta_file")
    pr_body=$(jq -r '.pr_body' "$meta_file")

    commits_list="${commits_list}
--- Commit $((c+1)) of $num_commits ---
SHA: $sha
Message: $subject"

    if [ -n "$pr_num" ] && [ "$pr_num" != "" ] && [ "$pr_num" != "null" ]; then
      commits_list="${commits_list}
PR #${pr_num}: ${pr_title}
PR description: ${pr_body}"
    fi

    commits_list="${commits_list}
Changed files:
${files_list}
"

    commit_files=$(jq -c '.files' "$meta_file" 2>/dev/null || echo "[]")
    all_commit_files=$(echo "$all_commit_files" "$commit_files" | jq -s '.[0] + .[1]')
  done

  rm -rf "$commit_meta_dir"

  # ---- Single agent call for all commits ----
  candidate_fixes="[]"

  agent_output="$(mktemp)"
  if cursor_agent_from_template "$BATCH_PROMPT_TEMPLATE" "$agent_output" \
       "TEST_NAME=$test_name" \
       "FAILURE_SIGNATURE=$failure_sig" \
       "WORKFLOW_PATH=$wf_path" \
       "TEST_LAYER=$test_layer" \
       "COMMITS_LIST=$commits_list"; then

    # Parse agent response — expect a JSON array
    if jq 'type == "array"' "$agent_output" 2>/dev/null | grep -q true; then
      num_agent_fixes=$(jq 'length' "$agent_output")
      for af in $(seq 0 $((num_agent_fixes - 1))); do
        is_fix=$(jq -r ".[$af].is_likely_fix // false" "$agent_output")
        if [ "$is_fix" = "true" ]; then
          sha=$(jq -r ".[$af].sha" "$agent_output")
          confidence=$(jq -r ".[$af].fix_confidence // \"low\"" "$agent_output")
          layer=$(jq -r ".[$af].fix_layer // \"unknown\"" "$agent_output")
          reasoning=$(jq -r ".[$af].reasoning // \"\"" "$agent_output")

          # Look up commit files from the meta we already gathered
          commit_files_json="[]"
          for c in $(seq 0 $((num_commits - 1))); do
            check_sha=$(jq -r ".[$c].sha" "$commits_file")
            if [ "$check_sha" = "$sha" ]; then
              commit_msg=$(jq -r ".[$c].subject" "$commits_file")
              break
            fi
          done

          log_info "      Commit $sha: LIKELY FIX (layer=$layer, confidence=$confidence)"

          candidate_fixes=$(echo "$candidate_fixes" | jq \
            --arg sha "$sha" \
            --arg msg "${commit_msg:-}" \
            --arg layer "$layer" \
            --arg conf "$confidence" \
            --arg reason "$reasoning" \
            '. += [{"sha": $sha, "message": $msg, "files_changed": [], "fix_layer": $layer, "confidence": $conf, "reasoning": $reason}]')
        fi
      done
    else
      log_warn "    Agent did not return a JSON array — attempting single object parse"
      is_fix=$(jq -r '.is_likely_fix // false' "$agent_output" 2>/dev/null || echo "false")
      if [ "$is_fix" = "true" ]; then
        sha=$(jq -r '.sha' "$agent_output")
        confidence=$(jq -r '.fix_confidence // "low"' "$agent_output")
        layer=$(jq -r '.fix_layer // "unknown"' "$agent_output")
        reasoning=$(jq -r '.reasoning // ""' "$agent_output")
        candidate_fixes=$(echo "$candidate_fixes" | jq \
          --arg sha "$sha" --arg layer "$layer" --arg conf "$confidence" --arg reason "$reasoning" \
          '. += [{"sha": $sha, "message": "", "files_changed": [], "fix_layer": $layer, "confidence": $conf, "reasoning": $reason}]')
      fi
    fi
  else
    log_warn "    Batch agent call failed"
  fi
  rm -f "$agent_output"

  # Fallback: file-based heuristic if agent found nothing
  num_fixes=$(echo "$candidate_fixes" | jq 'length')

  if [ "$num_fixes" -eq 0 ]; then
    log_info "    No specific fix commit identified by agent — using file-based heuristic"

    if ! echo "$all_commit_files" | jq 'if type == "array" and all(type == "string") then true else false end' 2>/dev/null | grep -q true; then
      all_commit_files="[]"
    fi

    dominant_layer=$(be_dominant_layer "$all_commit_files" 2>/dev/null || echo "unknown")

    candidate_fixes=$(jq -n \
      --arg sha "$(jq -r '.[-1].sha' "$commits_file")" \
      --arg msg "$(jq -r '.[-1].subject' "$commits_file")" \
      --argjson files "$all_commit_files" \
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

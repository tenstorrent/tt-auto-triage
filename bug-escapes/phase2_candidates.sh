#!/usr/bin/env bash
set -euo pipefail

# Phase 2: Identify Candidate Failures
#
# For each "other" workflow in pipeline-config.json:
#   1. Fetch recent runs (~2 weeks) from main branch
#   2. Get per-job results and group by job name
#   3. Identify jobs with N+ consecutive failures (N = CONSECUTIVE_RUNS)
#   4. Download failure logs and invoke Cursor agent to verify determinism
#   5. Write confirmed consistent failures to consistent-failures.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
PIPELINE_CONFIG="$OUTPUT_DIR/pipeline-config.json"
FAILURES_OUTPUT="$OUTPUT_DIR/consistent-failures.json"
PROMPT_TEMPLATE="$SCRIPT_DIR/prompts/verify_consistent_failure.txt"
LOGS_DIR="$OUTPUT_DIR/logs"

CONSECUTIVE_RUNS="${CONSECUTIVE_RUNS:-3}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-14}"
MAX_CANDIDATES="${MAX_CANDIDATES:-999}"
MAX_LOG_BYTES="${MAX_LOG_BYTES:-100000}"
MAX_RUNS_PER_WORKFLOW="${MAX_RUNS_PER_WORKFLOW:-50}"

mkdir -p "$LOGS_DIR"

# Initialize output
echo '[]' > "$FAILURES_OUTPUT"

# Get "other" workflows from pipeline config
other_workflows=$(jq -c '[.workflows[] | select(.classification == "other")]' "$PIPELINE_CONFIG")
num_workflows=$(echo "$other_workflows" | jq 'length')
log_info "Phase 2: scanning $num_workflows 'other' workflows for consecutive failures"

cutoff_date=$(date -u -d "-${LOOKBACK_DAYS} days" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null \
  || date -u -v "-${LOOKBACK_DAYS}d" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null \
  || date -u '+%Y-%m-%dT%H:%M:%SZ')

for i in $(seq 0 $((num_workflows - 1))); do
  wf_entry=$(echo "$other_workflows" | jq -c ".[$i]")
  wf_path=$(echo "$wf_entry" | jq -r '.path')
  test_layer=$(echo "$wf_entry" | jq -r '.test_layer')
  wf_basename=$(basename "$wf_path")

  log_info "  [$((i+1))/$num_workflows] $wf_path (layer=$test_layer)"

  # Resolve workflow to numeric ID
  wf_id=$(cached_get_workflow_id "$wf_basename")
  if [ -z "$wf_id" ]; then
    log_warn "    Could not resolve workflow ID for $wf_basename — skipping"
    continue
  fi

  # Fetch recent runs — cap to MAX_RUNS_PER_WORKFLOW to avoid massive API call overhead
  # when building per-job timelines (1 API call per run for job data).
  all_runs="[]"
  page=1
  max_pages=5
  while [ "$page" -le "$max_pages" ]; do
    page_json=$(get_workflow_runs "$wf_id" "$page")
    runs_on_page=$(echo "$page_json" | jq '.workflow_runs | length' 2>/dev/null || echo 0)
    if [ "$runs_on_page" -eq 0 ]; then
      break
    fi

    all_runs=$(echo "$all_runs" "$page_json" | jq -s '
      .[0] + (.[1].workflow_runs // [])
    ')

    current_count=$(echo "$all_runs" | jq 'length')
    if [ "$current_count" -ge "$MAX_RUNS_PER_WORKFLOW" ]; then
      all_runs=$(echo "$all_runs" | jq --argjson cap "$MAX_RUNS_PER_WORKFLOW" '.[:$cap]')
      break
    fi

    oldest_run_date=$(echo "$page_json" | jq -r '.workflow_runs[-1].created_at // empty' 2>/dev/null || echo "")
    if [ -n "$oldest_run_date" ] && [[ "$oldest_run_date" < "$cutoff_date" ]]; then
      break
    fi
    page=$((page + 1))
  done

  num_runs=$(echo "$all_runs" | jq 'length')
  log_info "    Fetched $num_runs runs (cap: $MAX_RUNS_PER_WORKFLOW)"

  if [ "$num_runs" -lt "$CONSECUTIVE_RUNS" ]; then
    log_info "    Not enough runs — skipping"
    continue
  fi

  # Fetch jobs for all runs in parallel (up to 8 concurrent), then merge
  # into a per-job timeline: { "job_name": [ {run_id, conclusion, created_at}, ... ] }
  jobs_tmp_dir="$(mktemp -d)"

  for r in $(seq 0 $((num_runs - 1))); do
    run_id=$(echo "$all_runs" | jq -r ".[$r].id")
    run_date=$(echo "$all_runs" | jq -r ".[$r].created_at")
    (
      jobs_json=$(get_jobs_for_run "$run_id" 2>/dev/null || echo '{"jobs":[]}')
      echo "$jobs_json" | jq -c --arg rdate "$run_date" --argjson rid "$run_id" '
        [.jobs[]? | {job: .name, job_id: .id, run_id: $rid, conclusion: (.conclusion // "unknown"), created_at: $rdate}]
      ' > "$jobs_tmp_dir/run_${run_id}.json" 2>/dev/null || echo '[]' > "$jobs_tmp_dir/run_${run_id}.json"
    ) &
    # Limit to 8 parallel fetches
    if (( (r + 1) % 8 == 0 )); then
      wait
    fi
  done
  wait

  # Merge all per-run job arrays into a single timeline object.
  # Normalize job names: strip "workflow-name / " prefix so that
  # "galaxy-e2e-tests / BH Galaxy CCL tests" groups with "BH Galaxy CCL tests".
  job_timeline_file="$(mktemp)"
  cat "$jobs_tmp_dir"/run_*.json 2>/dev/null | jq -s '
    [.[][] | {
      job: (if (.job | contains(" / ")) then (.job | split(" / ") | .[-1]) else .job end),
      raw_job: .job,
      job_id: (.job_id // 0),
      run_id: (.run_id // 0),
      conclusion,
      created_at
    }] |
    group_by(.job) |
    map({key: .[0].job, value: .}) |
    from_entries
  ' > "$job_timeline_file" 2>/dev/null || echo '{}' > "$job_timeline_file"
  rm -rf "$jobs_tmp_dir"

  # Find jobs with N+ consecutive failures
  # Runs are ordered newest-first from the API, so we scan for consecutive "failure" conclusions
  candidate_jobs=$(jq -c --argjson n "$CONSECUTIVE_RUNS" '
    to_entries | map(
      .key as $name | .value as $runs |
      ($runs | sort_by(.created_at)) as $sorted |
      # Find all windows of N consecutive failures
      [range(0; ($sorted | length) - $n + 1)] |
      map(
        . as $start |
        [$sorted[$start:$start + $n][].conclusion] |
        if all(. == "failure") then $start
        else null end
      ) | map(select(. != null)) |
      # Prefer windows followed by a success (newest first for those).
      # Fall back to any window if none have a subsequent success.
      (map(
        . as $start |
        if ($start + $n) < ($sorted | length) then
          if $sorted[$start + $n].conclusion == "success" then
            {"job": $name, "failing_runs": [$sorted[$start:$start + $n][] | {run_id, job_id}]}
          else null end
        else null end
      ) | map(select(. != null)) | reverse) as $with_fix |
      if ($with_fix | length) > 0 then $with_fix[0]
      else
        # No window has a subsequent success — pick the newest window anyway
        (. | reverse | .[0]) as $last_start |
        {"job": $name, "failing_runs": [$sorted[$last_start:$last_start + $n][] | {run_id, job_id}]}
      end
    ) | map(select(. != null))
  ' "$job_timeline_file")

  # Filter out infrastructure/build jobs that are never bug escapes
  candidate_jobs=$(echo "$candidate_jobs" | jq -c '[.[] | select(
    ((.job | startswith("build-artifact")) or
     (.job | startswith("resolve-artifacts")) or
     (.job == "tests-to-run") or
     (.job | endswith("load-test-matrix")) or
     (.job | contains("define-ops-tests")) or
     (.job | contains("define-demo-tests")) or
     (.job | contains("define-ttsim")))
    | not
  )]')

  num_candidates=$(echo "$candidate_jobs" | jq 'length')

  if [ "$num_candidates" -eq 0 ]; then
    rm -f "$job_timeline_file"
    log_info "    No consecutive failures found (after filtering infrastructure jobs)"
    continue
  fi

  # Flakiness pre-filter: score each candidate using its full timeline.
  # Two signals:
  #   1. likely_flaky — moderate failure rate with frequent pass/fail alternation
  #   2. streak_starts_at_window_edge — the failure streak already existed at the start of
  #      the lookback window (first run in the timeline is also a failure). This is a
  #      pre-existing failure, not a new regression — mark it separately so downstream
  #      phases treat it with lower confidence.
  candidate_jobs=$(echo "$candidate_jobs" | jq -c --slurpfile tl "$job_timeline_file" '
    [.[] | . as $cand |
      ($tl[0][$cand.job] // []) as $runs |
      ($runs | sort_by(.created_at)) as $sorted |
      ($sorted | length) as $total |
      if $total < 3 then . + {"likely_flaky": false, "flake_score": 0, "streak_starts_at_window_edge": false}
      else
        ([$sorted[].conclusion | select(. == "failure")] | length) as $fails |
        ($fails / $total) as $ratio |
        # Count alternations: how many times conclusion differs from the previous run
        ([range(1; $total)] | map(
          if $sorted[.].conclusion != $sorted[. - 1].conclusion then 1 else 0 end
        ) | add // 0) as $alternations |
        ($alternations / ($total - 1)) as $alt_rate |
        # Flaky: moderate failure rate + frequent alternation
        ($ratio > 0.2 and $ratio < 0.8 and $alt_rate > 0.3) as $is_flaky |
        # Pre-existing: first run in timeline is already a failure
        ($sorted[0].conclusion == "failure") as $edge_fail |
        . + {"likely_flaky": $is_flaky, "flake_score": $alt_rate,
             "streak_starts_at_window_edge": $edge_fail}
      end
    ]
  ')

  flaky_count=$(echo "$candidate_jobs" | jq '[.[] | select(.likely_flaky == true)] | length')
  if [ "$flaky_count" -gt 0 ]; then
    log_info "    Flakiness filter: $flaky_count of $num_candidates candidates marked as likely_flaky"
  fi

  rm -f "$job_timeline_file"

  log_info "    Found $num_candidates candidate job(s) with ${CONSECUTIVE_RUNS}+ consecutive failures (infrastructure filtered)"

  # Download logs and build candidate list for the agent.
  # The agent will search the log files itself — no excerpt extraction needed.
  candidates_summary=""
  candidates_meta=()
  included=0

  for c in $(seq 0 $((num_candidates - 1))); do

    candidate=$(echo "$candidate_jobs" | jq -c ".[$c]")
    job_name=$(echo "$candidate" | jq -r '.job')
    failing_runs_arr=$(echo "$candidate" | jq -c '.failing_runs')

    # Download logs for at least one failing run
    log_dir_found=""
    for try_run_id in $(echo "$failing_runs_arr" | jq -r '.[].run_id'); do
      run_log_dir="$LOGS_DIR/run_${try_run_id}"
      if [ ! -d "$run_log_dir" ]; then
        download_run_logs "$try_run_id" "$run_log_dir" || {
          log_warn "      Could not download logs for run $try_run_id"
          continue
        }
      fi
      if [ -d "$run_log_dir" ]; then
        log_dir_found="$run_log_dir"
        break
      fi
    done

    if [ -z "$log_dir_found" ]; then
      log_info "      Could not download logs for '$job_name' — skipping"
      continue
    fi

    candidates_summary="${candidates_summary}
=== CANDIDATE $((included + 1)): ${job_name} ===
Failing run IDs: $(echo "$failing_runs_arr" | jq -r '[.[].run_id | tostring] | join(", ")')
Log directory: ${log_dir_found}

"
    candidates_meta+=("$(echo "$candidate" | jq -c '.')")
    included=$((included + 1))
  done

  if [ "$included" -eq 0 ]; then
    log_info "    No logs downloaded — skipping workflow"
    continue
  fi

  log_info "    Sending $included candidates to agent for batch classification"

  # Single agent call for this workflow
  agent_output="$(mktemp)"
  if cursor_agent_from_template "$PROMPT_TEMPLATE" "$agent_output" \
       "WORKFLOW_PATH=$wf_path" \
       "TEST_LAYER=$test_layer" \
       "CONSECUTIVE_RUNS=$CONSECUTIVE_RUNS" \
       "CANDIDATES_SUMMARY=$candidates_summary"; then

    # The agent returns a JSON array. Iterate and match back by job name.
    num_results=$(jq 'if type == "array" then length else 0 end' "$agent_output" 2>/dev/null || echo 0)
    log_info "    Agent returned $num_results classifications"

    for idx in $(seq 0 $((num_results - 1))); do
      result=$(jq -c ".[$idx]" "$agent_output" 2>/dev/null || echo "{}")

      is_test_fail=$(echo "$result" | jq -r 'if .is_test_failure == null then false else .is_test_failure end' 2>/dev/null || echo "false")
      is_infra=$(echo "$result" | jq -r 'if .is_infrastructure_noise == null then true else .is_infrastructure_noise end' 2>/dev/null || echo "true")

      if [ "$is_test_fail" != "true" ] || [ "$is_infra" = "true" ]; then
        continue
      fi

      agent_job=$(echo "$result" | jq -r '.job // ""' 2>/dev/null || echo "")
      test_name=$(echo "$result" | jq -r '.test_name // "null"' 2>/dev/null || echo "null")
      failure_sig=$(echo "$result" | jq -r '.failure_signature // "unknown"' 2>/dev/null || echo "unknown")
      confidence=$(echo "$result" | jq -r '.confidence // "low"' 2>/dev/null || echo "low")
      reasoning=$(echo "$result" | jq -r '.reasoning // ""' 2>/dev/null || echo "")

      if [ "$test_name" = "null" ] || [ "$test_name" = "unknown" ] || [ -z "$test_name" ]; then
        log_info "      Skipping '$agent_job': agent confirmed failure but couldn't identify the test name"
        continue
      fi

      # Match back to candidate metadata to get failing_run_ids
      matched_meta=""
      for m in "${candidates_meta[@]}"; do
        meta_job=$(echo "$m" | jq -r '.job')
        if [ "$meta_job" = "$agent_job" ]; then
          matched_meta="$m"
          break
        fi
      done

      if [ -z "$matched_meta" ]; then
        log_warn "      Agent returned job '$agent_job' not found in candidates — skipping"
        continue
      fi

      failing_run_ids=$(echo "$matched_meta" | jq -c '.failing_runs')
      is_flaky=$(echo "$matched_meta" | jq -r '.likely_flaky // false')
      flake_score=$(echo "$matched_meta" | jq -r '.flake_score // 0')

      # Refine test_layer from the test path when possible (more specific than workflow-level)
      effective_test_layer="$test_layer"
      test_file_path="${test_name%%::*}"
      refined_layer=$(be_file_to_layer "$test_file_path" 2>/dev/null || echo "unknown")
      if [ "$refined_layer" != "unknown" ] && [ -n "$refined_layer" ]; then
        effective_test_layer="$refined_layer"
      fi

      log_info "      CONFIRMED: $test_name ($agent_job, confidence=$confidence, flaky=$is_flaky, layer=$effective_test_layer)"

      jq --arg wf "$wf_path" \
         --arg job "$agent_job" \
         --arg tn "$test_name" \
         --arg fs "$failure_sig" \
         --argjson frid "$failing_run_ids" \
         --arg tl "$effective_test_layer" \
         --arg conf "$confidence" \
         --arg notes "$reasoning" \
         --argjson flaky "$is_flaky" \
         --argjson fscore "$flake_score" \
         '. += [{"workflow": $wf, "job": $job, "test_name": $tn, "failure_signature": $fs, "failing_run_ids": $frid, "test_layer": $tl, "agent_confidence": $conf, "agent_notes": $notes, "likely_flaky": $flaky, "flake_score": $fscore}]' \
         "$FAILURES_OUTPUT" > "${FAILURES_OUTPUT}.tmp" && mv "${FAILURES_OUTPUT}.tmp" "$FAILURES_OUTPUT"

      total_confirmed=$(jq 'length' "$FAILURES_OUTPUT")
      if [ "$total_confirmed" -ge "$MAX_CANDIDATES" ]; then
        log_info "      Reached MAX_CANDIDATES=$MAX_CANDIDATES — stopping Phase 2 early"
        rm -f "$agent_output"
        break 2
      fi
    done
  else
    log_warn "    Agent call failed for workflow $wf_path — skipping"
  fi

  rm -f "$agent_output"
done

total_failures=$(jq 'length' "$FAILURES_OUTPUT")
log_info "Phase 2 done: $total_failures confirmed consistent failures"

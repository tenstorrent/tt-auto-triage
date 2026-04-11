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
MAX_RUNS_PER_WORKFLOW="${MAX_RUNS_PER_WORKFLOW:-30}"

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
  wf_id=$(get_workflow_id "$wf_basename" 2>/dev/null || echo "")
  if [ -z "$wf_id" ]; then
    log_warn "    Could not resolve workflow ID for $wf_basename — skipping"
    continue
  fi

  # Fetch recent runs — cap to MAX_RUNS_PER_WORKFLOW to avoid massive API call overhead
  # when building per-job timelines (1 API call per run for job data).
  all_runs="[]"
  page=1
  max_pages=3
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

  # For each run, get jobs and build a per-job timeline
  # Structure: { "job_name": [ {run_id, conclusion, run_created_at}, ... ] }
  job_timeline_file="$(mktemp)"
  echo '{}' > "$job_timeline_file"

  for r in $(seq 0 $((num_runs - 1))); do
    run_id=$(echo "$all_runs" | jq -r ".[$r].id")
    run_date=$(echo "$all_runs" | jq -r ".[$r].created_at")

    jobs_json=$(get_jobs_for_run "$run_id")
    num_jobs=$(echo "$jobs_json" | jq '.jobs | length' 2>/dev/null || echo 0)

    for j in $(seq 0 $((num_jobs - 1))); do
      job_name=$(echo "$jobs_json" | jq -r ".jobs[$j].name")
      conclusion=$(echo "$jobs_json" | jq -r ".jobs[$j].conclusion // \"unknown\"")

      jq --arg jn "$job_name" \
         --arg rid "$run_id" \
         --arg conc "$conclusion" \
         --arg rdate "$run_date" \
         'if .[$jn] then
            .[$jn] += [{"run_id": ($rid | tonumber), "conclusion": $conc, "created_at": $rdate}]
          else
            .[$jn] = [{"run_id": ($rid | tonumber), "conclusion": $conc, "created_at": $rdate}]
          end' \
         "$job_timeline_file" > "${job_timeline_file}.tmp" && mv "${job_timeline_file}.tmp" "$job_timeline_file"
    done
  done

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
            {"job": $name, "failing_runs": [$sorted[$start:$start + $n][].run_id]}
          else null end
        else null end
      ) | map(select(. != null)) | reverse) as $with_fix |
      if ($with_fix | length) > 0 then $with_fix[0]
      else
        # No window has a subsequent success — pick the newest window anyway
        (. | reverse | .[0]) as $last_start |
        {"job": $name, "failing_runs": [$sorted[$last_start:$last_start + $n][].run_id]}
      end
    ) | map(select(. != null))
  ' "$job_timeline_file")

  num_candidates=$(echo "$candidate_jobs" | jq 'length')
  rm -f "$job_timeline_file"

  if [ "$num_candidates" -eq 0 ]; then
    log_info "    No consecutive failures found"
    continue
  fi

  log_info "    Found $num_candidates candidate job(s) with ${CONSECUTIVE_RUNS}+ consecutive failures"

  # Collect error excerpts from up to 20 candidates, then batch-classify
  # with a single agent call.
  max_candidates_per_prompt=20
  candidates_summary=""
  candidates_meta=()  # parallel arrays for metadata
  excerpt_bytes=2000
  included=0

  for c in $(seq 0 $((num_candidates - 1))); do
    if [ "$included" -ge "$max_candidates_per_prompt" ]; then
      break
    fi

    candidate=$(echo "$candidate_jobs" | jq -c ".[$c]")
    job_name=$(echo "$candidate" | jq -r '.job')
    failing_run_ids=$(echo "$candidate" | jq -c '.failing_runs')
    first_run_id=$(echo "$failing_run_ids" | jq -r '.[0]')

    # Extract the job name suffix for log file matching
    if [[ "$job_name" == *" / "* ]]; then
      job_filter="${job_name##* / }"
    else
      job_filter="$job_name"
    fi

    # Download logs for the first failing run (cached if already downloaded)
    run_log_dir="$LOGS_DIR/run_${first_run_id}"
    if [ ! -d "$run_log_dir" ]; then
      download_run_logs "$first_run_id" "$run_log_dir" || {
        log_warn "      Could not download logs for run $first_run_id — skipping $job_name"
        continue
      }
    fi

    # Extract an error-focused excerpt from the job-specific log.
    # Search for common error markers and grab context around them;
    # fall back to a section before the final cleanup if no markers found.
    excerpt=""
    for logfile in "$run_log_dir"/*"${job_filter}"* "$run_log_dir"/**/*"${job_filter}"*; do
      [ -f "$logfile" ] || continue
      # Try to find error-relevant lines using common CI failure markers
      error_line=$(grep -n -m1 -iE 'FAILED|TT_FATAL|AssertionError|RuntimeError|Error:|CRASHED|fatal error|test.*failed' "$logfile" 2>/dev/null | head -1 | cut -d: -f1 || true)
      if [ -n "$error_line" ]; then
        # Extract a window: 5 lines before through ~60 lines after the error
        start_line=$((error_line > 5 ? error_line - 5 : 1))
        excerpt="$(sed -n "${start_line},$((start_line + 65))p" "$logfile" | head -c "$excerpt_bytes")"
      else
        # No error marker found — take from 3/4 through the file (skip cleanup at end)
        file_size=$(wc -c < "$logfile")
        skip_to=$(( file_size * 3 / 4 ))
        if [ "$skip_to" -gt "$excerpt_bytes" ]; then
          excerpt="$(tail -c +"$skip_to" "$logfile" | head -c "$excerpt_bytes")"
        else
          excerpt="$(tail -c "$excerpt_bytes" "$logfile")"
        fi
      fi
      break
    done

    if [ -z "$excerpt" ]; then
      log_info "      No matching log for '$job_name' in run $first_run_id — skipping"
      continue
    fi

    candidates_summary="${candidates_summary}
=== CANDIDATE $((included + 1)): ${job_name} ===
Failing run IDs: $(echo "$failing_run_ids" | jq -r 'join(", ")')
Error excerpt (last ${excerpt_bytes} bytes of run ${first_run_id}):
${excerpt}

"
    candidates_meta+=("$(echo "$candidate" | jq -c --arg jn "$job_name" '.')")
    included=$((included + 1))
  done

  if [ "$included" -eq 0 ]; then
    log_info "    No log excerpts collected — skipping workflow"
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
      test_name=$(echo "$result" | jq -r '.test_name // "unknown"' 2>/dev/null || echo "unknown")
      failure_sig=$(echo "$result" | jq -r '.failure_signature // "unknown"' 2>/dev/null || echo "unknown")
      confidence=$(echo "$result" | jq -r '.confidence // "low"' 2>/dev/null || echo "low")
      reasoning=$(echo "$result" | jq -r '.reasoning // ""' 2>/dev/null || echo "")

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
      log_info "      CONFIRMED: $test_name ($agent_job, confidence=$confidence)"

      jq --arg wf "$wf_path" \
         --arg job "$agent_job" \
         --arg tn "$test_name" \
         --arg fs "$failure_sig" \
         --argjson frid "$failing_run_ids" \
         --arg tl "$test_layer" \
         --arg conf "$confidence" \
         --arg notes "$reasoning" \
         '. += [{"workflow": $wf, "job": $job, "test_name": $tn, "failure_signature": $fs, "failing_run_ids": $frid, "test_layer": $tl, "agent_confidence": $conf, "agent_notes": $notes}]' \
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

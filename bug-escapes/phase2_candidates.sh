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

  # For each candidate, download logs and invoke the Cursor agent
  for c in $(seq 0 $((num_candidates - 1))); do
    candidate=$(echo "$candidate_jobs" | jq -c ".[$c]")
    job_name=$(echo "$candidate" | jq -r '.job')
    failing_run_ids=$(echo "$candidate" | jq -c '.failing_runs')
    run_ids_str=$(echo "$failing_run_ids" | jq -r 'join(", ")')

    log_info "      Checking job '$job_name' (runs: $run_ids_str)"

    # Per-run agent analysis: call the agent once per run with the full
    # MAX_LOG_BYTES budget, then programmatically check consistency.
    if [[ "$job_name" == *" / "* ]]; then
      job_filter="${job_name##* / }"
    else
      job_filter="$job_name"
    fi

    run_results=()
    all_runs_analyzed=true

    for run_id in $(echo "$failing_run_ids" | jq -r '.[]'); do
      run_log_dir="$LOGS_DIR/run_${run_id}"
      if [ ! -d "$run_log_dir" ]; then
        download_run_logs "$run_id" "$run_log_dir" || {
          log_warn "        Could not download logs for run $run_id"
          all_runs_analyzed=false
          continue
        }
      fi

      logs_content=""
      logs_bytes=0
      for logfile in "$run_log_dir"/*"${job_filter}"* "$run_log_dir"/**/*"${job_filter}"*; do
        [ -f "$logfile" ] || continue
        remaining=$((MAX_LOG_BYTES - logs_bytes))
        if [ "$remaining" -le 100 ]; then
          break
        fi
        file_header="--- $(basename "$logfile") ---
"
        file_chunk="$(tail -c "$remaining" "$logfile")"
        logs_content="${logs_content}${file_header}${file_chunk}
"
        logs_bytes=$((logs_bytes + ${#file_header} + ${#file_chunk} + 1))
      done

      if [ -z "$logs_content" ]; then
        log_warn "        No logs for run $run_id — skipping"
        all_runs_analyzed=false
        continue
      fi
      log_info "        Run $run_id: ${logs_bytes} bytes of logs (cap: ${MAX_LOG_BYTES})"

      agent_output="$(mktemp)"
      if cursor_agent_from_template "$PROMPT_TEMPLATE" "$agent_output" \
           "WORKFLOW_PATH=$wf_path" \
           "JOB_NAME=$job_name" \
           "RUN_ID=$run_id" \
           "LOGS_CONTENT=$logs_content"; then

        is_infra=$(jq -r 'if .is_infrastructure_noise == null then true else .is_infrastructure_noise end' "$agent_output" 2>/dev/null || echo "true")
        is_test_fail=$(jq -r 'if .is_test_failure == null then false else .is_test_failure end' "$agent_output" 2>/dev/null || echo "false")
        tn=$(jq -r '.test_name // "null"' "$agent_output" 2>/dev/null || echo "null")
        fs=$(jq -r '.failure_signature // "null"' "$agent_output" 2>/dev/null || echo "null")
        conf=$(jq -r '.confidence // "low"' "$agent_output" 2>/dev/null || echo "low")
        reason=$(jq -r '.reasoning // ""' "$agent_output" 2>/dev/null || echo "")

        if [ "$is_infra" = "true" ] || [ "$is_test_fail" != "true" ]; then
          log_info "        Run $run_id: infrastructure noise or not a test failure — skipping candidate"
          rm -f "$agent_output"
          all_runs_analyzed=false
          break
        fi

        log_info "        Run $run_id: test_name='$tn' signature='${fs:0:80}' (confidence=$conf)"
        run_results+=("$(jq -c '.' "$agent_output")")
      else
        log_warn "        Agent call failed for run $run_id — skipping candidate"
        all_runs_analyzed=false
        rm -f "$agent_output"
        break
      fi
      rm -f "$agent_output"
    done

    num_results=${#run_results[@]}
    num_expected=$(echo "$failing_run_ids" | jq 'length')

    if [ "$num_results" -lt "$num_expected" ]; then
      log_info "        Only $num_results/$num_expected runs analyzed — skipping candidate"
      continue
    fi

    # Programmatic consistency check: compare test_name across all runs.
    # Use the first run's test_name as the reference; check if all others
    # contain it as a substring or vice versa (fuzzy match).
    first_test_name=$(echo "${run_results[0]}" | jq -r '.test_name // "null"')
    is_consistent=true
    for idx in $(seq 1 $((num_results - 1))); do
      other_test_name=$(echo "${run_results[$idx]}" | jq -r '.test_name // "null"')
      if [ "$first_test_name" = "null" ] || [ "$other_test_name" = "null" ]; then
        is_consistent=false
        break
      fi
      if [ "$first_test_name" != "$other_test_name" ]; then
        # Fuzzy: check substring containment in either direction
        if [[ "$first_test_name" == *"$other_test_name"* ]] || [[ "$other_test_name" == *"$first_test_name"* ]]; then
          :
        else
          is_consistent=false
          break
        fi
      fi
    done

    if [ "$is_consistent" != "true" ]; then
      log_info "        Test names differ across runs — not a consistent failure"
      continue
    fi

    test_name="$first_test_name"
    failure_sig=$(echo "${run_results[0]}" | jq -r '.failure_signature // "unknown"')
    confidence=$(echo "${run_results[0]}" | jq -r '.confidence // "low"')
    reasoning=$(echo "${run_results[0]}" | jq -r '.reasoning // ""')

    log_info "        CONFIRMED consistent failure: $test_name (confidence=$confidence)"

    jq --arg wf "$wf_path" \
       --arg job "$job_name" \
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
      log_info "        Reached MAX_CANDIDATES=$MAX_CANDIDATES — stopping Phase 2 early"
      break 3
    fi
  done
done

total_failures=$(jq 'length' "$FAILURES_OUTPUT")
log_info "Phase 2 done: $total_failures confirmed consistent failures"

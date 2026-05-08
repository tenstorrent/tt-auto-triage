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
# MAX_SNIPPET_BYTES: cap each candidate's log snippet before adding to the LLM
# prompt. Performance/nightly tests can produce tensor diffs and multi-KB stack
# traces; 20 such candidates in a chunk easily exceeds the 2MB ARG_MAX limit.
# 6000 bytes ≈ 30 typical log lines — sufficient for failure type classification.
MAX_SNIPPET_BYTES="${MAX_SNIPPET_BYTES:-6000}"
MAX_RUNS_PER_WORKFLOW="${MAX_RUNS_PER_WORKFLOW:-50}"
PHASE2_CHUNK_SIZE="${PHASE2_CHUNK_SIZE:-20}"
# INFRA_NOISE_RECHECK_HOURS: force re-check of cached infra_noise entries older
# than this many hours. Guards against wrong LLM classification silencing a real
# failure for the full 48h TTL. Default: 24h (re-examine persistent failures daily).
INFRA_NOISE_RECHECK_HOURS="${INFRA_NOISE_RECHECK_HOURS:-24}"
# NO_LOGS_RECHECK_HOURS: force re-check of cached no_logs entries older than
# this many hours. no_logs is often transient (in-progress runs whose ZIP isn't
# available yet); retry sooner than the 48h TTL. Default: 4h.
NO_LOGS_RECHECK_HOURS="${NO_LOGS_RECHECK_HOURS:-4}"

# ── Persistent seen-candidate cache ────────────────────────────────────────
# Keyed by "workflow_basename|job_name|sorted_run_ids". Verdicts:
#   no_logs     - logs not downloadable (stale/expired)
#   infra_noise - LLM or grep found no real test failure
#   confirmed   - LLM confirmed a real test failure
# Pulled from remote at start so distributed workers share state.
SEEN_CACHE_FILE="$SCRIPT_DIR/state/seen.json"
SEEN_CACHE_UPDATED=false
mkdir -p "$SCRIPT_DIR/state"
echo '{}' > "$SEEN_CACHE_FILE"

# Download the latest seen-candidate cache artifact from the tt-metal repo.
# The artifact is uploaded at the end of each Phase 2 run (see bug-escapes-ci.yaml).
# Falls back silently to an empty cache if no artifact exists yet.
_restore_seen_cache() {
  local token="${GITHUB_TOKEN:-}"
  [ -z "$token" ] && return
  local artifact_id
  artifact_id=$(curl -s -H "Authorization: Bearer $token" \
    "https://api.github.com/repos/tenstorrent/tt-metal/actions/artifacts?name=bug-escapes-seen-cache&per_page=1" \
    | jq -r '.artifacts[0].id // empty' 2>/dev/null || echo "")
  [ -z "$artifact_id" ] && { log_info "Seen cache: no prior artifact found (first run)"; return; }
  local tmpzip
  tmpzip=$(mktemp --suffix=.zip)
  if curl -s -H "Authorization: Bearer $token" -L \
       "https://api.github.com/repos/tenstorrent/tt-metal/actions/artifacts/${artifact_id}/zip" \
       -o "$tmpzip" 2>/dev/null && [ -s "$tmpzip" ]; then
    if unzip -p "$tmpzip" "seen.json" > "${SEEN_CACHE_FILE}.dl" 2>/dev/null; then
      jq -s '.[0] * .[1]' "$SEEN_CACHE_FILE" "${SEEN_CACHE_FILE}.dl" \
        > "${SEEN_CACHE_FILE}.merged" 2>/dev/null \
        && mv "${SEEN_CACHE_FILE}.merged" "$SEEN_CACHE_FILE" || true
      rm -f "${SEEN_CACHE_FILE}.dl"
    fi
  fi
  rm -f "$tmpzip"
}
_restore_seen_cache

# Evict stale entries after loading the remote cache.
# Exact-key entries (not _noisy) now use {"v":"verdict","t":"ISO_ts"} format.
# Old-format string entries are expired immediately (no timestamp = unknown age).
# _noisy entries keep their own 6-hour TTL enforced by _is_job_noisy().
# EXACT_KEY_TTL_HOURS: exact-key entries older than this are evicted (default: 48h).
EXACT_KEY_TTL_HOURS="${EXACT_KEY_TTL_HOURS:-48}"
_evict_stale_entries() {
  local now_s ttl_s before_count after_count
  now_s=$(date -u +%s)
  ttl_s=$(( EXACT_KEY_TTL_HOURS * 3600 ))
  before_count=$(jq 'length' "$SEEN_CACHE_FILE")
  jq --argjson now "$now_s" --argjson ttl "$ttl_s" '
    with_entries(
      select(
        # Keep all _noisy entries (TTL enforced by _is_job_noisy())
        (.key | startswith("_noisy|")) or
        # Keep new-format exact-key entries within TTL
        (
          (.value | type == "object") and
          (.value.t != null) and
          (($now - ((.value.t | strptime("%Y-%m-%dT%H:%M:%SZ") | mktime)? // 0)) < $ttl)
        )
        # Old-format string entries: silently dropped (stale, no timestamp)
      )
    )
  ' "$SEEN_CACHE_FILE" > "${SEEN_CACHE_FILE}.tmp" \
    && mv "${SEEN_CACHE_FILE}.tmp" "$SEEN_CACHE_FILE"
  after_count=$(jq 'length' "$SEEN_CACHE_FILE")
  local evicted=$(( before_count - after_count ))
  if [ "$evicted" -gt 0 ]; then
    log_info "Seen cache: evicted $evicted stale entries (${before_count} → ${after_count})"
    SEEN_CACHE_UPDATED=true
  fi
}
_evict_stale_entries
log_info "Seen cache loaded: $(jq 'length' "$SEEN_CACHE_FILE") entries (TTL=${EXACT_KEY_TTL_HOURS}h)"

_mark_seen() {
  local key="$1" verdict="$2"
  local now_ts
  now_ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  jq --arg k "$key" --arg v "$verdict" --arg t "$now_ts" \
    '. + {($k): {"v": $v, "t": $t}}' "$SEEN_CACHE_FILE" \
    > "${SEEN_CACHE_FILE}.tmp" && mv "${SEEN_CACHE_FILE}.tmp" "$SEEN_CACHE_FILE"
  SEEN_CACHE_UPDATED=true
}

_is_seen() {
  # Only new-format {"v":"...","t":"..."} objects are considered seen.
  # Old-format string entries (no timestamp) are treated as expired and ignored.
  local result
  result=$(jq -r --arg k "$1" '(.[$k] // null) | if type == "object" and .v != null then "yes" else "no" end' "$SEEN_CACHE_FILE")
  [ "$result" = "yes" ]
}

# Job-level noise index — keyed by "_noisy|wf_basename|job_name", value is ISO timestamp.
# Used to skip log downloads for jobs that have been consistently empty/undownloadable,
# avoiding repeated zip fetches when the run-ID window shifts each hour.
# Only set for empty-snippet and no-logs paths (not LLM-classified infra noise, since
# those had real log content and could later produce real test failures).
# TTL: 6 hours — if a job starts producing real errors, we catch it within one cron cycle.
_mark_job_noisy() {
  local wf="$1" job="$2"
  local noisy_key="_noisy|${wf}|${job}"
  local now_ts
  now_ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  jq --arg k "$noisy_key" --arg v "$now_ts" '. + {($k): $v}' "$SEEN_CACHE_FILE" \
    > "${SEEN_CACHE_FILE}.tmp" && mv "${SEEN_CACHE_FILE}.tmp" "$SEEN_CACHE_FILE"
  SEEN_CACHE_UPDATED=true
}

_is_job_noisy() {
  local wf="$1" job="$2"
  local noisy_key="_noisy|${wf}|${job}"
  local ts
  ts=$(jq -r --arg k "$noisy_key" '.[$k] // ""' "$SEEN_CACHE_FILE")
  [ -z "$ts" ] && return 1
  local now_ts last_ts
  now_ts=$(date -u +%s)
  last_ts=$(date -u -d "$ts" +%s 2>/dev/null \
    || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s 2>/dev/null \
    || echo 0)
  [ $(( now_ts - last_ts )) -lt 21600 ]  # 6 hours
}

_candidate_key() {
  local wf="$1" job="$2" runs_json="$3"
  local sorted_ids
  sorted_ids=$(echo "$runs_json" | jq -r '[.[].run_id | tostring] | sort | join(",")' 2>/dev/null || echo "")
  printf '%s|%s|%s' "$wf" "$job" "$sorted_ids"
}
# ───────────────────────────────────────────────────────────────────────────

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

  # Strictly filter to the lookback window. The pagination loop stops as soon as
  # the oldest run on a page crosses cutoff_date, but may have collected up to
  # MAX_RUNS_PER_WORKFLOW runs before checking. Filter here so we only fetch job
  # timelines for runs actually within the window — this is the main API call budget.
  all_runs=$(echo "$all_runs" | jq --arg cutoff "$cutoff_date" '[.[] | select(.created_at >= $cutoff)]')

  num_runs=$(echo "$all_runs" | jq 'length')
  log_info "    Fetched $num_runs runs in lookback window"

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
  candidates_summaries=()
  candidates_meta=()
  candidates_keys=()
  included=0

  for c in $(seq 0 $((num_candidates - 1))); do

    candidate=$(echo "$candidate_jobs" | jq -c ".[$c]")
    job_name=$(echo "$candidate" | jq -r '.job')
    failing_runs_arr=$(echo "$candidate" | jq -c '.failing_runs')

    # Skip candidates already classified in a prior run.
    # Exception: if the cached verdict is infra_noise and the entry is older than
    # INFRA_NOISE_RECHECK_HOURS (default 24h), force a re-check.  This prevents
    # a wrong infra_noise classification from silencing a real failure for the full
    # 48h TTL — a persistent failure will be re-examined at least once per day.
    cand_key=$(_candidate_key "$wf_basename" "$job_name" "$failing_runs_arr")
    if _is_seen "$cand_key"; then
      _cached_verdict=$(jq -r --arg k "$cand_key" '.[$k].v // "unknown"' "$SEEN_CACHE_FILE" 2>/dev/null || echo "unknown")
      _cached_ts=$(jq -r --arg k "$cand_key" '.[$k].t // ""' "$SEEN_CACHE_FILE" 2>/dev/null || echo "")
      _force_recheck=false
      if [ -n "$_cached_ts" ]; then
        _cached_s=$(date -u -d "$_cached_ts" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$_cached_ts" +%s 2>/dev/null || echo 0)
        _age_h=$(( ($(date -u +%s) - _cached_s) / 3600 ))
        if [ "$_cached_verdict" = "infra_noise" ] && [ "$_age_h" -ge "${INFRA_NOISE_RECHECK_HOURS:-24}" ]; then
          _force_recheck=true
          log_info "      '$job_name' cached infra_noise is ${_age_h}h old — forcing re-check"
        elif [ "$_cached_verdict" = "no_logs" ] && [ "$_age_h" -ge "${NO_LOGS_RECHECK_HOURS:-4}" ]; then
          # no_logs is often transient (in-progress parent run). Retry after a short window.
          _force_recheck=true
          log_info "      '$job_name' cached no_logs is ${_age_h}h old — forcing re-check"
        fi
      fi
      if [ "$_force_recheck" = "false" ]; then
        log_info "      '$job_name' already classified — skipping (cached)"
        continue
      fi
    fi

    # Skip log download for jobs that have been consistently empty within the last 6h.
    # These jobs (infra setup, condition-eval-only) never produce test output, so
    # downloading their logs each hour (as run IDs shift) is pure I/O waste.
    if _is_job_noisy "$wf_basename" "$job_name"; then
      log_info "      '$job_name' known-noisy job — skipping log download"
      _mark_seen "$cand_key" "infra_noise"
      continue
    fi

    # Download logs for failing runs; try each until we find error lines.
    # Strategy: prefer job-level log download (smaller, works for in-progress runs,
    # avoids cross-job noise from run-level ZIPs that contain 50+ jobs). Fall back
    # to run-level ZIP only when job_id is unavailable (0/null).
    # This prevents misclassifying a job as "infra noise" when:
    #   a) The parent run is still in-progress so ZIP isn't available yet
    #   b) The log tail is dominated by other failing jobs in the same run
    log_dir_found=""
    log_snippet=""

    # Write TSV to a temp file instead of using process substitution < <(jq ...).
    # In bash 5.x, while...done < <(cmd) propagates the subprocess exit code through
    # set -e even when the jq command itself succeeds — causing a silent script exit
    # if jq produces no output or the process substitution races with set -e cleanup.
    _runs_tsv=$(mktemp)
    echo "$failing_runs_arr" | jq -r '.[] | [.run_id, (.job_id // 0)] | @tsv' > "$_runs_tsv" 2>/dev/null || true

    while IFS=$'\t' read -r try_run_id try_job_id; do
      run_log_dir="$LOGS_DIR/run_${try_run_id}"
      if [ ! -d "$run_log_dir" ]; then
        # Primary: job-level log (works even when run is still in-progress)
        _job_download_ok=false
        if [ -n "$try_job_id" ] && [ "$try_job_id" != "0" ] && [ "$try_job_id" != "null" ]; then
          job_log_file="$run_log_dir/job_${try_job_id}.txt"
          mkdir -p "$run_log_dir"
          if gh api "repos/${AT_OWNER_REPO}/actions/jobs/${try_job_id}/logs" \
               > "$job_log_file" 2>/dev/null && [ -s "$job_log_file" ]; then
            _job_download_ok=true
          else
            rm -f "$job_log_file"
            rmdir "$run_log_dir" 2>/dev/null || true
          fi
        fi
        # Fallback: run-level ZIP (works only for completed runs)
        if [ "$_job_download_ok" = "false" ]; then
          download_run_logs "$try_run_id" "$run_log_dir" || {
            log_warn "      Could not download logs for run $try_run_id (job=$try_job_id)"
            # Remove empty dir so next hourly run retries (don't cache failed downloads)
            rmdir "$run_log_dir" 2>/dev/null || true
            continue
          }
        fi
      fi
      if [ -d "$run_log_dir" ]; then
        # Remember the first successfully downloaded run as fallback
        if [ -z "$log_dir_found" ]; then
          log_dir_found="$run_log_dir"
        fi

        # Search all actual log files recursively, excluding system.txt (GitHub runner
        # metadata containing only condition evaluation text like "Evaluating job.if" —
        # never test output). The flat numbered files in run_log_dir root (e.g.
        # "0_JobName.txt") are the full job logs; job subdirectories only have system.txt.
        # Grep full file content so failures at any position are caught.
        # Filter out common false-positives that contain "error" in non-error context:
        #   - C++ include paths (runtime/sfpi/compiler/..., riscv-tt-elf/.../error_constants.h)
        #   - apt-get package names (liberror-perl, libstdc++, libedit2, etc.)
        #   - Docker digest-mismatch lines (harmless OCI annotation warnings)
        candidate_snippet=$(find "$run_log_dir" -type f -name "*.txt" ! -name "system.txt" 2>/dev/null \
          | sort \
          | xargs grep -ih "FAILED\|TT_FATAL\|TT_THROW\|AssertionError\|RuntimeError\|ERROR:\|Error:\|exit code [1-9]\|non-zero exit\|[Kk]illed\|[Tt]raceback\|[Ss]egmentation fault\|CUDA error\|pytest.*FAILED\|FAIL \|[Hh]ealth check.*[Ff]ailed\|[Hh]ealth checks failed\|[Tt]imeout\|[Cc]onnection refused\|[Cc]annot connect\|runner.*lost\|[Ll]ost communication" 2>/dev/null \
          | grep -v "runtime/sfpi/compiler/\|riscv-tt-elf/\|liberror-perl\|libstdc++\|libedit2\|digest-mismatch\|##\[endgroup\]\|\.hpp\|\.h:[0-9]" \
          | tail -40 \
          || true)
        if [ -n "$candidate_snippet" ]; then
          log_snippet="$candidate_snippet"
          break  # Found error lines — no need to try more runs
        fi
      fi
    done < "$_runs_tsv"
    rm -f "$_runs_tsv"

    if [ -z "$log_dir_found" ]; then
      log_info "      Could not download logs for '$job_name' — skipping"
      # Only cache the no_logs verdict (window-scoped), do NOT mark job noisy.
      # Transient download failures (e.g. in-progress runs) should be retried
      # next hour with a fresh window, not suppressed for 6h.
      _mark_seen "$cand_key" "no_logs"
      continue
    fi

    # If grep found no error lines, skip LLM entirely — sending a blank snippet
    # to the agent is pure token waste; it can only say "infra noise" anyway.
    # Also mark the job as known-noisy so future hourly runs skip the download.
    if [ -z "$log_snippet" ]; then
      log_info "      No error lines in logs for '$job_name' — skipping LLM (infra noise)"
      _mark_seen "$cand_key" "infra_noise"
      _mark_job_noisy "$wf_basename" "$job_name"
      continue
    fi

    # Pytest short-circuit: if the snippet contains a pytest-style FAILED line
    # (e.g. "FAILED tests/nightly/.../test_foo.py::test_bar[params] - AssertionError")
    # this is deterministically a real test failure — no LLM judgment needed.
    # The LLM has historically misclassified these as infra_noise when the snippet
    # also contains innocuous "error" strings from C++ paths or apt output.
    pytest_fail_line=$(echo "$log_snippet" | grep -iE "^FAILED [a-zA-Z_./].*\.py(::|$)|FAILED [a-zA-Z_./].*\.py::" | head -1 || true)
    if [ -n "$pytest_fail_line" ]; then
      # Extract test name: everything before " - " on the FAILED line
      _pytest_test_name=$(echo "$pytest_fail_line" | sed 's/^FAILED //' | sed 's/ - .*//' | xargs)
      # Extract failure signature: the part after " - " on the same line
      _pytest_failure_sig=$(echo "$pytest_fail_line" | sed 's/^FAILED [^ ]* - //' | xargs 2>/dev/null || echo "")
      if [ -z "$_pytest_failure_sig" ]; then
        _pytest_failure_sig=$(echo "$log_snippet" | grep -iE "^E\s+|AssertionError:|TT_FATAL|RuntimeError:" | tail -1 | xargs 2>/dev/null || echo "test assertion failure")
      fi

      # Refine test_layer from the test path
      _pytest_effective_layer="$test_layer"
      _pytest_file_path="${_pytest_test_name%%::*}"
      _pytest_refined=$(be_file_to_layer "$_pytest_file_path" 2>/dev/null || echo "unknown")
      if [ "$_pytest_refined" != "unknown" ] && [ -n "$_pytest_refined" ]; then
        _pytest_effective_layer="$_pytest_refined"
      fi

      is_flaky=$(echo "$candidate" | jq -r '.likely_flaky // false')
      flake_score=$(echo "$candidate" | jq -r '.flake_score // 0')

      log_info "      CONFIRMED (pytest): $_pytest_test_name (layer=$_pytest_effective_layer, flaky=$is_flaky)"
      _mark_seen "$cand_key" "confirmed"

      jq --arg wf "$wf_path" \
         --arg job "$job_name" \
         --arg tn "$_pytest_test_name" \
         --arg fs "$_pytest_failure_sig" \
         --argjson frid "$failing_runs_arr" \
         --arg tl "$_pytest_effective_layer" \
         --arg conf "high" \
         --arg notes "Detected via pytest FAILED line (deterministic short-circuit, no LLM)" \
         --argjson flaky "$is_flaky" \
         --argjson fscore "$flake_score" \
         '. += [{"workflow": $wf, "job": $job, "test_name": $tn, "failure_signature": $fs, "failing_run_ids": $frid, "test_layer": $tl, "agent_confidence": $conf, "agent_notes": $notes, "likely_flaky": $flaky, "flake_score": $fscore}]' \
         "$FAILURES_OUTPUT" > "${FAILURES_OUTPUT}.tmp" && mv "${FAILURES_OUTPUT}.tmp" "$FAILURES_OUTPUT"

      total_confirmed=$(jq 'length' "$FAILURES_OUTPUT")
      if [ "$total_confirmed" -ge "$MAX_CANDIDATES" ]; then
        log_info "      Reached MAX_CANDIDATES=$MAX_CANDIDATES — stopping Phase 2 early"
        break 2  # break out of both the candidate loop and workflow loop
      fi
      continue
    fi

    # Truncate snippet to MAX_SNIPPET_BYTES to keep the chunk prompt under
    # the 2MB ARG_MAX limit (performance/nightly workflows produce very verbose
    # error output — tensor diffs, long stack traces — that can push 20 candidates
    # over the limit even after env is stripped with env -i in cursor_agent.sh).
    _snippet_for_prompt="${log_snippet:0:${MAX_SNIPPET_BYTES}}"
    candidates_summaries+=("
=== CANDIDATE $((included + 1)): ${job_name} ===
Failing run IDs: $(echo "$failing_runs_arr" | jq -r '[.[].run_id | tostring] | join(", ")')
Pre-extracted error lines (grep output from log files):
${_snippet_for_prompt}

")
    candidates_meta+=("$(echo "$candidate" | jq -c '.')")
    candidates_keys+=("$cand_key")
    included=$((included + 1))
  done

  if [ "$included" -eq 0 ]; then
    log_info "    No logs downloaded — skipping workflow"
    continue
  fi

  # Split candidates into chunks to avoid LLM timeouts on large batches
  num_chunks=$(( (included + PHASE2_CHUNK_SIZE - 1) / PHASE2_CHUNK_SIZE ))
  log_info "    Sending $included candidates to ${LLM_BACKEND:-cursor} agent in $num_chunks chunk(s) of up to $PHASE2_CHUNK_SIZE"

  phase2_early_exit=false
  for chunk_idx in $(seq 0 $((num_chunks - 1))); do
    chunk_start=$((chunk_idx * PHASE2_CHUNK_SIZE))
    chunk_end=$(( chunk_start + PHASE2_CHUNK_SIZE - 1 ))
    if [ "$chunk_end" -ge "$included" ]; then
      chunk_end=$((included - 1))
    fi

    # Build summary string for this chunk
    chunk_summary=""
    for ci in $(seq "$chunk_start" "$chunk_end"); do
      chunk_summary="${chunk_summary}${candidates_summaries[$ci]}"
    done

    log_info "    Chunk $((chunk_idx+1))/$num_chunks: candidates $((chunk_start+1))-$((chunk_end+1))"

    agent_output="$(mktemp)"
    if cursor_agent_from_template "$PROMPT_TEMPLATE" "$agent_output" \
         "WORKFLOW_PATH=$wf_path" \
         "TEST_LAYER=$test_layer" \
         "CONSECUTIVE_RUNS=$CONSECUTIVE_RUNS" \
         "CANDIDATES_SUMMARY=$chunk_summary"; then

      # Mark all candidates in this chunk as infra_noise now that the LLM has
      # responded successfully.  Confirmed ones will be overwritten to "confirmed"
      # below.  We do this AFTER the LLM call (not before) so that a timeout or
      # bad response does not suppress real failures for up to EXACT_KEY_TTL_HOURS.
      for _pci in $(seq "$chunk_start" "$chunk_end"); do
        _mark_seen "${candidates_keys[$_pci]}" "infra_noise"
      done

      # The agent returns a JSON array. Iterate and match back by job name.
      # Use head -1 so a multi-doc output file (two arrays) doesn't produce
      # a multi-line num_results that breaks bash arithmetic below.
      num_results=$(jq 'if type == "array" then length else 0 end' "$agent_output" 2>/dev/null | head -1)
      num_results="${num_results:-0}"
      log_info "    ${LLM_BACKEND:-cursor} agent returned $num_results classifications"

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

        # Job-level fallback when the agent can't pin down a specific test name.
        # Many CI jobs are shell-script wrappers (TTNN tutorials, didt-tests,
        # sdpa-stress, etc.) whose error tail contains "exit code 1" or "Killed"
        # without a pytest/gtest identifier. Phase 3's fix-point search keys on
        # job_name (not test_name), so dropping these entirely loses real signal.
        # Instead, use agent_job as the identifier and floor confidence to "low"
        # so Phase 4's medium/high verify-matrix filter naturally excludes them
        # from auto-verification while still surfacing them in the report.
        if [ "$test_name" = "null" ] || [ "$test_name" = "unknown" ] || [ -z "$test_name" ]; then
          if [ -n "$agent_job" ]; then
            log_info "      Job-level confirmed (no test name extracted): '$agent_job' — using job name as identifier, confidence floored to low"
            test_name="$agent_job"
            confidence="low"
            reasoning="[no specific test name extractable from logs] $reasoning"
          else
            log_warn "      Skipping: agent confirmed failure but returned neither test_name nor job"
            continue
          fi
        fi

        # Match back to candidate metadata to get failing_run_ids and cache key
        matched_meta=""
        matched_key=""
        for _mi in "${!candidates_meta[@]}"; do
          meta_job=$(echo "${candidates_meta[$_mi]}" | jq -r '.job')
          if [ "$meta_job" = "$agent_job" ]; then
            matched_meta="${candidates_meta[$_mi]}"
            matched_key="${candidates_keys[$_mi]}"
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
        # Upgrade seen-cache verdict from pre-marked infra_noise → confirmed
        [ -n "$matched_key" ] && _mark_seen "$matched_key" "confirmed"

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
          phase2_early_exit=true
          break
        fi
      done
    else
      log_warn "    Agent call failed for chunk $((chunk_idx+1)) of $wf_path — skipping chunk"
    fi

    rm -f "$agent_output"
    if [ "$phase2_early_exit" = "true" ]; then
      break
    fi
  done
  if [ "$phase2_early_exit" = "true" ]; then
    break
  fi
done

total_failures=$(jq 'length' "$FAILURES_OUTPUT")
log_info "Phase 2 done: $total_failures confirmed consistent failures"

if [ "$SEEN_CACHE_UPDATED" = "true" ]; then
  log_info "Seen cache updated: $(jq 'length' "$SEEN_CACHE_FILE") entries — will be uploaded as artifact by workflow"
fi

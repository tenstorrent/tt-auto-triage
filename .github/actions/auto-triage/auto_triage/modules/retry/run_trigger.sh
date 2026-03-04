#!/bin/bash
#
# run_trigger.sh - Trigger and poll GitHub Actions job reruns
#
# Provides:
#   trigger_retry_run(job_id) -> 0 on success, 1 on failure
#   wait_for_run_completion(run_id, job_name, start_attempt, timeout_sec, [poll_interval]) -> status
#   find_job_in_attempt(run_id, attempt, job_name) -> job_id (echo to stdout, empty if not found)
#
# Status values: success, failure, cancelled, timeout, error
# Uses lib/github_api.sh (and thus lib/config.sh for AT_OWNER_REPO)
#
# Usage: source this file.
#

if [ -n "${_RUN_TRIGGER_LOADED:-}" ]; then
    return 0
fi
_RUN_TRIGGER_LOADED=1

_MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_MODULE_DIR}/../../lib"
# shellcheck source=../../lib/common.sh
[ -f "${_LIB_DIR}/common.sh" ] && source "${_LIB_DIR}/common.sh"
# shellcheck source=../../lib/github_api.sh
source "${_LIB_DIR}/github_api.sh"

# ==============================================================================
# trigger_retry_run(job_id) -> 0 on success, 1 on failure
#
# POST to /actions/jobs/{job_id}/rerun. Returns 0 if HTTP 200/201.
# ==============================================================================
trigger_retry_run() {
    local job_id="${1:-}"

    if [ -z "$job_id" ]; then
        log_error "trigger_retry_run: job_id required"
        return 1
    fi

    local response
    response=$(gh_api_post "repos/${AT_OWNER_REPO}/actions/jobs/${job_id}/rerun")
    local code
    code=$(echo "$response" | head -1 | awk '{print $2}')
    code="${code:-000}"

    if [ "$code" = "201" ] || [ "$code" = "200" ]; then
        return 0
    fi
    return 1
}

# ==============================================================================
# Normalize job name for matching (lowercase, unicode dashes -> ASCII)
# ==============================================================================
_run_trigger_normalize_name() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[–—−‐‑‒]/-/g'
}

# ==============================================================================
# Find job ID in jobs JSON by name (case-insensitive, handles unicode dashes)
# ==============================================================================
_run_trigger_find_job_by_name() {
    local jobs_json="$1"
    local job_name="$2"
    local name_norm
    name_norm=$(_run_trigger_normalize_name "$job_name")

    echo "$jobs_json" | jq -r --arg name "$name_norm" '
        def normalize: ascii_downcase | gsub("[–—−‐‑‒]"; "-");
        .jobs // [] |
        map(
            (.name | normalize) as $norm_job |
            select(
                $norm_job == $name or
                ($norm_job | contains($name)) or
                ($name | contains($norm_job))
            )
        ) |
        first | .id // empty
    ' 2>/dev/null || echo ""
}

# ==============================================================================
# find_job_in_attempt(run_id, attempt, job_name) -> job_id (stdout)
#
# Contract:
#   - Returns 1 only for invalid parameters (missing required args).
#   - Returns 0 and echoes empty string when job is not found or jobs cannot be retrieved.
# ==============================================================================
find_job_in_attempt() {
    local run_id="${1:-}"
    local attempt="${2:-}"
    local job_name="${3:-}"
    local jobs_json

    # Invalid parameters: fail with non-zero status and no output
    if [ -z "$run_id" ] || [ -z "$attempt" ] || [ -z "$job_name" ]; then
        log_error "find_job_in_attempt: run_id, attempt, and job_name are required"
        return 1
    fi

    # Retrieve jobs for the given run/attempt; ignore exit code and treat empty as "not found"
    jobs_json=$(get_jobs_for_run "$run_id" "$attempt" 2>/dev/null || true)

    if [ -z "$jobs_json" ]; then
        # No jobs data available: treat as "not found" (empty output, success status)
        echo ""
        return 0
    fi

    # Delegate to helper which echoes job ID or empty string; always return success here
    _run_trigger_find_job_by_name "$jobs_json" "$job_name" || true
    return 0
}

# ==============================================================================
# wait_for_run_completion(run_id, job_name, start_attempt, timeout_sec, [poll_interval]) -> status
#
# Waits for a new run attempt to appear, finds the job by name, polls until done.
# All waiting (for attempt + for job) counts against timeout_sec.
# Wait for new attempt is capped at MAX_WAIT_FOR_ATTEMPT (120s) so we fail fast
# if the retry never starts, matching retry_on_deterministic.sh behavior.
# Returns: success, failure, cancelled, timeout, error
# ==============================================================================
MAX_WAIT_FOR_ATTEMPT=120  # cap: don't wait >2 min for new attempt to appear

wait_for_run_completion() {
    local run_id="${1:-}"
    local job_name="${2:-}"
    local start_attempt="${3:-1}"
    local timeout_sec="${4:-10800}"  # default 3 hours
    local poll_interval="${5:-60}"

    if [ -z "$run_id" ] || [ -z "$job_name" ]; then
        log_error "wait_for_run_completion: run_id and job_name required"
        echo "error"
        return 1
    fi

    local expected_attempt=$((start_attempt + 1))
    local total_elapsed=0
    local new_attempt=""
    local wait_start_interval=10
    local max_wait_attempt=$(( MAX_WAIT_FOR_ATTEMPT < timeout_sec ? MAX_WAIT_FOR_ATTEMPT : timeout_sec ))

    log_info "Waiting for attempt ${expected_attempt} to appear (timeout: ${max_wait_attempt}s)..." >&2
    while [ $total_elapsed -lt $max_wait_attempt ]; do
        local run_info
        run_info=$(get_run_info "$run_id")
        new_attempt=$(echo "$run_info" | jq -r '.run_attempt // 1')
        if [ "$new_attempt" -ge "$expected_attempt" ]; then
            log_info "New attempt ${new_attempt} detected after ${total_elapsed}s" >&2
            break
        fi
        sleep "$wait_start_interval"
        total_elapsed=$((total_elapsed + wait_start_interval))
        log_info "Waiting for new attempt... (${total_elapsed}s / ${max_wait_attempt}s, current: ${new_attempt})" >&2
    done

    if [ -z "$new_attempt" ] || [ "$new_attempt" -lt "$expected_attempt" ]; then
        log_warn "Timed out waiting for attempt ${expected_attempt} after ${total_elapsed}s"
        echo "timeout"
        return 1
    fi

    # Brief wait for jobs to be created in new attempt
    sleep 5
    total_elapsed=$((total_elapsed + 5))

    local jobs_json
    jobs_json=$(get_jobs_for_run "$run_id" "$new_attempt")
    local poll_job_id
    poll_job_id=$(_run_trigger_find_job_by_name "$jobs_json" "$job_name")

    if [ -n "$poll_job_id" ]; then
        log_info "Found job ${poll_job_id} ('${job_name}') in attempt ${new_attempt}" >&2
    else
        log_info "Job '${job_name}' not yet visible in attempt ${new_attempt}, will keep looking..." >&2
    fi

    local status=""
    local conclusion=""
    local timeout_min=$((timeout_sec / 60))

    while [ $total_elapsed -lt $timeout_sec ]; do
        if [ -n "$poll_job_id" ]; then
            local job_info
            job_info=$(get_job_info "$poll_job_id")
            status=$(echo "$job_info" | jq -r '.status // "unknown"')
            conclusion=$(echo "$job_info" | jq -r '.conclusion // "null"')

            if [ "$status" = "completed" ] || [ "$conclusion" = "cancelled" ] || \
               [ "$conclusion" = "failure" ] || [ "$conclusion" = "success" ]; then
                log_info "Job completed: status=${status}, conclusion=${conclusion} (after ${total_elapsed}s)" >&2
                if [ "$conclusion" = "cancelled" ]; then
                    echo "cancelled"
                elif [ "$conclusion" = "success" ]; then
                    echo "success"
                elif [ "$conclusion" = "failure" ]; then
                    echo "failure"
                else
                    echo "$conclusion"
                fi
                return 0
            fi

            if [ "$status" = "unknown" ]; then
                log_error "Job status is 'unknown', treating as error" >&2
                echo "error"
                return 1
            fi

            local elapsed_min=$((total_elapsed / 60))
            log_info "Job still running... status=${status} (${elapsed_min}m / ${timeout_min}m elapsed)" >&2
        else
            jobs_json=$(get_jobs_for_run "$run_id" "$new_attempt")
            poll_job_id=$(_run_trigger_find_job_by_name "$jobs_json" "$job_name")

            if [ -n "$poll_job_id" ]; then
                log_info "Found job ${poll_job_id} ('${job_name}') in attempt ${new_attempt}" >&2
                sleep "$poll_interval"
                total_elapsed=$((total_elapsed + poll_interval))
                continue
            fi

            local run_info
            run_info=$(get_run_info "$run_id")
            local run_status
            local run_conclusion
            run_status=$(echo "$run_info" | jq -r '.status // "unknown"')
            run_conclusion=$(echo "$run_info" | jq -r '.conclusion // "null"')

            if [ "$run_status" = "completed" ] || \
               [ "$run_conclusion" = "cancelled" ] || \
               [ "$run_conclusion" = "failure" ] || \
               [ "$run_conclusion" = "success" ]; then
                log_error "Run completed (${run_conclusion}) but job '${job_name}' never appeared" >&2
                echo "error"
                return 1
            fi

            local elapsed_min=$((total_elapsed / 60))
            log_info "Waiting for job '${job_name}' to appear... (${elapsed_min}m / ${timeout_min}m elapsed)" >&2
        fi

        sleep "$poll_interval"
        total_elapsed=$((total_elapsed + poll_interval))
    done

    log_warn "Timed out after ${timeout_min}m waiting for job to complete"
    echo "timeout"
    return 1
}

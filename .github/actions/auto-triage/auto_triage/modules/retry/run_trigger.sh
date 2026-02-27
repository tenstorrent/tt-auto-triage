#!/bin/bash
#
# run_trigger.sh - Trigger and poll GitHub Actions job reruns
#
# Provides:
#   trigger_retry_run(job_id) -> 0 on success, 1 on failure
#   wait_for_run_completion(run_id, job_name, start_attempt, timeout_sec, [poll_interval]) -> status
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
        map(select(
            (.name | normalize) == $name or
            (.name | normalize | contains($name)) or
            ($name | contains(.name | normalize))
        )) |
        first | .id // empty
    ' 2>/dev/null || echo ""
}

# ==============================================================================
# wait_for_run_completion(run_id, job_name, start_attempt, timeout_sec, [poll_interval]) -> status
#
# Waits for a new run attempt to appear, finds the job by name, polls until done.
# All waiting (for attempt + for job) counts against timeout_sec.
# Returns: success, failure, cancelled, timeout, error
# ==============================================================================
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

    # Wait for new attempt to appear (counts against timeout_sec)
    while [ $total_elapsed -lt $timeout_sec ]; do
        local run_info
        run_info=$(get_run_info "$run_id")
        new_attempt=$(echo "$run_info" | jq -r '.run_attempt // 1')
        if [ "$new_attempt" -ge "$expected_attempt" ]; then
            break
        fi
        sleep "$wait_start_interval"
        total_elapsed=$((total_elapsed + wait_start_interval))
    done

    if [ -z "$new_attempt" ] || [ "$new_attempt" -lt "$expected_attempt" ]; then
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

    local status=""
    local conclusion=""

    while [ $total_elapsed -lt $timeout_sec ]; do
        if [ -n "$poll_job_id" ]; then
            local job_info
            job_info=$(get_job_info "$poll_job_id")
            status=$(echo "$job_info" | jq -r '.status // "unknown"')
            conclusion=$(echo "$job_info" | jq -r '.conclusion // "null"')

            if [ "$status" = "completed" ] || [ "$conclusion" = "cancelled" ] || \
               [ "$conclusion" = "failure" ] || [ "$conclusion" = "success" ]; then
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
                echo "error"
                return 1
            fi
        else
            # Try to find job for this attempt
            jobs_json=$(get_jobs_for_run "$run_id" "$new_attempt")
            poll_job_id=$(_run_trigger_find_job_by_name "$jobs_json" "$job_name")

            # If the job has just appeared, let the next loop iteration handle it
            if [ -n "$poll_job_id" ]; then
                sleep "$poll_interval"
                total_elapsed=$((total_elapsed + poll_interval))
                continue
            fi

            # Job still not found; check if the run has already completed.
            # If the run is completed but the job never appeared, treat as error.
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
                echo "error"
                return 1
            fi
        fi

        sleep "$poll_interval"
        total_elapsed=$((total_elapsed + poll_interval))
    done

    echo "timeout"
    return 1
}

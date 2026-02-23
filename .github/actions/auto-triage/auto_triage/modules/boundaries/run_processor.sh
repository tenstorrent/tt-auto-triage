#!/bin/bash
#
# run_processor.sh - Process workflow runs to find boundary (last success, first failure)
#
# Fetches workflow runs with pagination, filters by branch/status, applies cutoff
# commit filter, and uses job_matcher to find subjob runs. Sets globals with results.
#
# Prerequisites: gh CLI authenticated.
# Caller must define: write_cancel_and_exit(msg) before sourcing.
#
# Expected env/globals (set by caller): WORKFLOW_ID, SUBJOB_NAME, WORKFLOW_NAME,
#   REPO (or AT_OWNER_REPO), BASE_URL, CUTOFF_COMMIT, CUTOFF_RUN_CREATED_AT (optional ISO timestamp), PER_PAGE, FAILURE_LIMIT,
#   RUN_LIMIT_WITHOUT_SUCCESS, SUBJOB_MISSING_CANCEL_LIMIT.
# Optional: MAX_WORKFLOW_PAGES (default 50), MAX_JOB_PAGES (default 20) - safety limits to ensure loop termination.
#
# Output globals: SUBJOB_RUNS_JSON, LAST_SUCCESSFUL_*,
#   FIRST_FAILING_*, FOUND_SUCCESS, FOUND_FAILURE, EXCEEDED_FAILURE_LIMIT, BOUNDARY_STATUS, BOUNDARY_MESSAGE
#
# Usage: source this file, then call process_workflow_runs
#

if [ -n "${_RUN_PROCESSOR_LOADED:-}" ]; then
    return 0
fi
_RUN_PROCESSOR_LOADED=1

_RUN_PROCESSOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/github_api.sh
source "$_RUN_PROCESSOR_DIR/../../lib/github_api.sh"
# shellcheck source=job_matcher.sh
source "$_RUN_PROCESSOR_DIR/job_matcher.sh"

# Check if commit A is newer than commit B (A is descendant of B).
is_commit_newer() {
    local commit_a="$1" commit_b="$2"
    [ -n "$commit_a" ] && [ -n "$commit_b" ] || return 1
    [ "$commit_a" != "$commit_b" ] || return 1
    git merge-base --is-ancestor "$commit_b" "$commit_a" 2>/dev/null
}

# Process workflow runs: paginate, filter, match subjobs, accumulate results.
# Sets output globals. May call write_cancel_and_exit on unrecoverable errors.
process_workflow_runs() {
    local repo="${AT_OWNER_REPO:-${REPO}}"
    [ -n "$repo" ] || { echo "run_processor: REPO or AT_OWNER_REPO required" >&2; return 1; }
    [ -n "$WORKFLOW_ID" ] || { echo "run_processor: WORKFLOW_ID required" >&2; return 1; }
    [ -n "$SUBJOB_NAME" ] || { echo "run_processor: SUBJOB_NAME required" >&2; return 1; }
    [ -n "$WORKFLOW_NAME" ] || { echo "run_processor: WORKFLOW_NAME required" >&2; return 1; }

    local per_page="${PER_PAGE:-100}"
    local failure_limit="${FAILURE_LIMIT:-30}"
    local run_limit_without_success="${RUN_LIMIT_WITHOUT_SUCCESS:-100}"
    local subjob_missing_cancel_limit="${SUBJOB_MISSING_CANCEL_LIMIT:-50}"
    local max_pages="${MAX_WORKFLOW_PAGES:-50}"
    local max_job_pages="${MAX_JOB_PAGES:-20}"
    local base_url="${BASE_URL:-https://github.com/${repo}}"

    local page=1 processed=0
    local last_successful_run="" last_successful_run_id="" last_successful_commit="" last_successful_job_url=""
    local first_failing_run="" first_failing_run_id="" first_failing_commit="" first_failing_job_url=""
    local most_recent_failure_run="" most_recent_failure_run_id="" most_recent_failure_commit="" most_recent_failure_job_url=""
    local found_success=false stop_search=false consecutive_missing=0
    local failure_only_count=0 exceeded_failure_limit=false
    local subjob_runs_json='[]'

    while [ "$page" -le "$max_pages" ]; do
        local page_resp
        page_resp=$(gh_api "repos/${repo}/actions/workflows/${WORKFLOW_ID}/runs?branch=main&per_page=${per_page}&page=${page}" "")
        if [ -z "$page_resp" ]; then
            if [ "$page" -eq 1 ]; then
                write_cancel_and_exit "Could not fetch workflow runs for workflow '${WORKFLOW_NAME}' (check that the workflow exists and that permissions are correct)."
            fi
            break
        fi

        local runs_page
        runs_page=$(echo "$page_resp" | jq '.workflow_runs // []')
        local page_total
        page_total=$(echo "$runs_page" | jq 'length')

        if [ "$page_total" -eq 0 ]; then
            [ "$page" -ne 1 ] || { echo "run_processor: No workflow runs returned" >&2; return 1; }
            break
        fi

        local valid_page
        valid_page=$(echo "$runs_page" | jq -r "[.[] | select(.head_branch == \"main\" and ((.status == \"completed\") or (.status == \"in_progress\") or (.status == \"waiting\") or (.status == \"queued\")) and (.conclusion != \"cancelled\"))]")
        local valid_count
        valid_count=$(echo "$valid_page" | jq 'length')

        if [ "$valid_count" -eq 0 ]; then
            page=$((page + 1))
            continue
        fi

        local run_rows
        mapfile -t run_rows < <(echo "$valid_page" | jq -c '.[]')
        local run_data
        for run_data in "${run_rows[@]}"; do
            local found_job=false
            local run_id run_commit run_completed_at run_url
            run_id=$(echo "$run_data" | jq -r '.id')
            run_commit=$(echo "$run_data" | jq -r '.head_sha')
            run_completed_at=$(echo "$run_data" | jq -r '.updated_at // .run_started_at // "unknown"')
            run_url="${base_url}/actions/runs/${run_id}"

            processed=$((processed + 1))

            if [ -n "${CUTOFF_COMMIT:-}" ]; then
                if is_commit_newer "$run_commit" "$CUTOFF_COMMIT"; then
                    continue
                fi
            fi

            # Skip runs newer than cutoff (when CUTOFF_RUN_CREATED_AT is set for testing on fixed errors)
            # Use timestamp comparison - run IDs are not guaranteed to be monotonically increasing
            if [ -n "${CUTOFF_RUN_CREATED_AT:-}" ]; then
                local run_created_at=$(echo "$run_data" | jq -r '.run_started_at // .created_at // ""')
                if [ -n "$run_created_at" ] && [[ "$run_created_at" > "$CUTOFF_RUN_CREATED_AT" ]]; then
                    continue
                fi
            fi

            local run_attempt
            run_attempt=$(echo "$run_data" | jq -r '.run_attempt // 1')
            local matching_jobs='[]'
            local attempt=$run_attempt

            while [ "$attempt" -ge 1 ]; do
                local page_j=1
                while [ "$page_j" -le "$max_job_pages" ]; do
                    local endpoint
                    if [ "$attempt" -eq "$run_attempt" ]; then
                        endpoint="repos/${repo}/actions/runs/${run_id}/jobs?per_page=${per_page}&page=${page_j}"
                    else
                        endpoint="repos/${repo}/actions/runs/${run_id}/attempts/${attempt}/jobs?per_page=${per_page}&page=${page_j}"
                    fi
                    local page_jobs
                    page_jobs=$(gh_api "$endpoint" "")

                    [ -n "$page_jobs" ] || break
                    local job_entries
                    job_entries=$(echo "$page_jobs" | jq '.jobs // []')
                    local job_count
                    job_count=$(echo "$job_entries" | jq 'length' 2>/dev/null || echo "0")
                    [ "$job_count" -ne 0 ] || break

                    local job_rows
                    mapfile -t job_rows < <(echo "$job_entries" | jq -c '.[]')
                    local job_item
                    for job_item in "${job_rows[@]}"; do
                        local job_name job_status job_conclusion
                        job_name=$(echo "$job_item" | jq -r '.name // ""')
                        job_status=$(echo "$job_item" | jq -r '.status // ""')
                        job_conclusion=$(echo "$job_item" | jq -r '.conclusion // "null"')

                        if [ "$job_status" != "completed" ]; then
                            continue
                        fi
                        match_subjob "$job_name" "$SUBJOB_NAME" "$WORKFLOW_NAME" || continue

                        found_job=true
                        local job_id job_attempt job_completed_at job_url entry_completed_at
                        job_id=$(echo "$job_item" | jq -r '.id')
                        job_attempt=$(echo "$job_item" | jq -r '.run_attempt // 1')
                        job_completed_at=$(echo "$job_item" | jq -r '.completed_at // empty')
                        job_url="${base_url}/actions/runs/${run_id}/job/${job_id}"
                        entry_completed_at="$run_completed_at"
                        [ -z "$job_completed_at" ] || entry_completed_at="$job_completed_at"

                        if [ "$job_conclusion" = "success" ]; then
                            if [ "$found_success" = false ]; then
                                last_successful_run="$run_url"
                                last_successful_run_id="$run_id"
                                last_successful_commit="$run_commit"
                                last_successful_job_url="$job_url"
                                found_success=true
                                subjob_runs_json=$(jq -n \
                                    --arg status "success" \
                                    --arg run_url "$run_url" \
                                    --arg job_url "$job_url" \
                                    --arg run_id "$run_id" \
                                    --arg job_id "$job_id" \
                                    --arg commit "$run_commit" \
                                    --arg completed_at "$entry_completed_at" \
                                    --argjson job_attempt "$job_attempt" \
                                    --argjson arr "$subjob_runs_json" \
                                    --argjson run_number "$processed" \
                                    '$arr + [{status:$status, run_url:$run_url, job_url:$job_url, run_id:$run_id, job_id:$job_id, commit:$commit, completed_at:$completed_at, job_attempt:$job_attempt, run_number:$run_number}]')
                                if [ -n "$most_recent_failure_run" ]; then
                                    first_failing_run="$most_recent_failure_run"
                                    first_failing_run_id="$most_recent_failure_run_id"
                                    first_failing_commit="$most_recent_failure_commit"
                                    first_failing_job_url="$most_recent_failure_job_url"
                                fi
                                stop_search=true
                                break
                            fi
                        elif [ "$job_conclusion" = "failure" ]; then
                            most_recent_failure_run="$run_url"
                            most_recent_failure_run_id="$run_id"
                            most_recent_failure_commit="$run_commit"
                            most_recent_failure_job_url="$job_url"
                            subjob_runs_json=$(jq -n \
                                --arg status "failure" \
                                --arg run_url "$run_url" \
                                --arg job_url "$job_url" \
                                --arg run_id "$run_id" \
                                --arg job_id "$job_id" \
                                --arg commit "$run_commit" \
                                --arg completed_at "$entry_completed_at" \
                                --argjson job_attempt "$job_attempt" \
                                --argjson arr "$subjob_runs_json" \
                                --argjson run_number "$processed" \
                                '$arr + [{status:$status, run_url:$run_url, job_url:$job_url, run_id:$run_id, job_id:$job_id, commit:$commit, completed_at:$completed_at, job_attempt:$job_attempt, run_number:$run_number}]')
                            if [ "$found_success" = false ]; then
                                failure_only_count=$((failure_only_count + 1))
                                if [ "$failure_only_count" -ge "$failure_limit" ]; then
                                    exceeded_failure_limit=true
                                    stop_search=true
                                    break
                                fi
                            fi
                        fi
                    done
                    [ "$found_success" = true ] || [ "$stop_search" = true ] && break

                    [ "$job_count" -lt "$per_page" ] && break
                    page_j=$((page_j + 1))
                done
                [ "$found_success" = true ] || [ "$stop_search" = true ] && break
                attempt=$((attempt - 1))
            done

            if [ "$found_job" = false ]; then
                # Cancel after N consecutive runs without subjob (job renamed/removed from workflow)
                consecutive_missing=$((consecutive_missing + 1))
                if [ "$consecutive_missing" -ge "$subjob_missing_cancel_limit" ]; then
                    write_cancel_and_exit "Subjob '${SUBJOB_NAME}' was not found in ${subjob_missing_cancel_limit} consecutive main-branch runs of workflow '${WORKFLOW_NAME}'. Please verify the job name."
                fi
                continue
            fi
            consecutive_missing=0

            if [ "$found_success" = false ] && [ "$processed" -ge "$run_limit_without_success" ] && [ -n "$most_recent_failure_run" ]; then
                exceeded_failure_limit=true
                stop_search=true
            fi

            [ "$stop_search" = false ] || break
        done

        [ "$found_success" = true ] || [ "$stop_search" = true ] && break
        page=$((page + 1))
    done

    # Sort and assign run numbers for subjob_runs_json
    if [ "$subjob_runs_json" != "[]" ]; then
        subjob_runs_json=$(echo "$subjob_runs_json" | jq '
            # Ensure completed_at exists for sort
            def normalize: map(.completed_at = (.completed_at // ""));
            # Add 0-based run_number from index
            def with_run_numbers: to_entries | map(.value + {run_number: .key});

            (normalize | map(select(.status == "success")) | sort_by(.completed_at) | first) as $success |
            (normalize | map(select(.status != "success")) | sort_by(.completed_at)) as $fails |
            (
                if $success == null
                then (normalize | sort_by(.completed_at))
                else [$success] + $fails
                end
                | with_run_numbers
            )
        ')
    fi

    local boundary_status="ok" boundary_message=""
    if [ "$page" -gt "$max_pages" ]; then
        boundary_status="page_limit_exceeded"
        boundary_message="Reached safety limit of ${max_pages} workflow run pages without finding a successful run. Consider increasing MAX_WORKFLOW_PAGES."
    elif [ "$exceeded_failure_limit" = true ]; then
        boundary_status="failure_limit_exceeded"
        boundary_message="More than ${failure_limit} failed runs were scanned without finding a successful run. The commit window is too old—default to Case 2 or Case 3."
    elif [ "$found_success" = false ]; then
        boundary_status="no_success_found"
        boundary_message="No successful runs were found within the current history window."
    fi

    # Export to globals for caller
    SUBJOB_RUNS_JSON="$subjob_runs_json"
    LAST_SUCCESSFUL_RUN="$last_successful_run"
    LAST_SUCCESSFUL_RUN_ID="$last_successful_run_id"
    LAST_SUCCESSFUL_COMMIT="$last_successful_commit"
    LAST_SUCCESSFUL_JOB_URL="$last_successful_job_url"
    FIRST_FAILING_RUN="$first_failing_run"
    FIRST_FAILING_RUN_ID="$first_failing_run_id"
    FIRST_FAILING_COMMIT="$first_failing_commit"
    FIRST_FAILING_JOB_URL="$first_failing_job_url"
    FOUND_SUCCESS="$found_success"
    FOUND_FAILURE="false"
    [ -z "$first_failing_run" ] || FOUND_FAILURE="true"
    EXCEEDED_FAILURE_LIMIT="$exceeded_failure_limit"
    BOUNDARY_STATUS="$boundary_status"
    BOUNDARY_MESSAGE="$boundary_message"
}

#!/bin/bash
#
# retry_orchestrator.sh - Main flow for deterministic retry: eligibility → trigger → wait → compare → update
#
# Sources: hardware_checker, run_trigger, result_comparator, slack_api, common, config
# Usage: Called by scripts/retry_on_deterministic.sh with: job_name workflow_name [slack_ts]
#

set -euo pipefail

TEST_MODE="${TEST_MODE:-false}"

# Resolve root: modules/retry/ -> auto_triage (dir containing lib, modules)
ORCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${ORCH_DIR}/../.." && pwd)"
AUTO_TRIAGE_ROOT="$ROOT"

# Source libs
# shellcheck source=../../lib/config.sh
source "${ORCH_DIR}/../../lib/config.sh"
# shellcheck source=../../lib/common.sh
source "${ORCH_DIR}/../../lib/common.sh"
# shellcheck source=../../lib/slack_api.sh
source "${ORCH_DIR}/../../lib/slack_api.sh"
# shellcheck source=hardware_checker.sh
source "${ORCH_DIR}/hardware_checker.sh"
# shellcheck source=run_trigger.sh
source "${ORCH_DIR}/run_trigger.sh"
# shellcheck source=result_comparator.sh
source "${ORCH_DIR}/result_comparator.sh"

JOB_NAME="${1:?Usage: $0 <job_name> <workflow_name> [slack_ts]}"
WORKFLOW_NAME="${2:?}"
SLACK_TS="${3:-}"

DATA_DIR="$(get_data_dir "$ROOT")"
OUTPUT_DIR="$(get_output_dir "$ROOT")"
LOGS_DIR="$(get_logs_dir "$ROOT")"
SLACK_MSG_PATH="${OUTPUT_DIR}/slack_message.json"
EXPLANATION_PATH="${OUTPUT_DIR}/explanation.md"
SUBJOB_RUNS_PATH="${DATA_DIR}/subjob_runs.json"
RETRY_RESULT_FILE="${DATA_DIR}/retry_result.json"
COMPARISON_FILE="${DATA_DIR}/error_comparison.json"
MAX_DURATION_SECONDS=$((3 * 60 * 60))
MAX_WAIT_MINUTES=180

send_notification() {
    if [ -n "$SLACK_TS" ]; then
        log_info "Sending threaded Slack notification..."
        send_slack_thread "$1" "$SLACK_TS"
    else
        log_info "Sending Slack notification..."
        send_slack_message "$1"
    fi
}

mkdir -p "$DATA_DIR"
echo '{"result": "no_retry", "message": ""}' > "$RETRY_RESULT_FILE"
SCENARIO=""
[ -f "$SLACK_MSG_PATH" ] && SCENARIO=$(jq -r '.scenario // ""' "$SLACK_MSG_PATH")
[ -z "$SCENARIO" ] && [ "$TEST_MODE" != "true" ] && { log_warn "No slack_message.json found, skipping retry"; exit 0; }
[ -z "$SCENARIO" ] && [ "$TEST_MODE" = "true" ] && SCENARIO="(cancelled/no analysis)"
log_info "Scenario: $SCENARIO"

# --- Eligibility ---
if [ "$TEST_MODE" != "true" ]; then
    SL=$(echo "$SCENARIO" | tr '[:upper:]' '[:lower:]')
    [[ "$SL" == *"non-deterministic"* || "$SL" == *"non deterministic"* || "$SL" == *"outside tt-metal"* || "$SL" == *"case 3"* ]] && \
        { log_warn "Not Case 1/4 (non-deterministic/Case 3), skipping"; exit 0; }
    IS_14="false"
    [[ "$SL" == *"deterministic"* || "$SL" == *"culprit"* || "$SL" == *"identified"* || "$SL" == *"case 1"* || "$SL" == *"case 4"* ]] && IS_14="true"
    [ "$IS_14" = "false" ] && [ -f "$SLACK_MSG_PATH" ] && \
        [ "$(jq -r '.commits // [] | map(select(.confidence != null and .confidence > 0)) | length' "$SLACK_MSG_PATH" 2>/dev/null || echo 0)" -gt 0 ] && IS_14="true"
    [ "$IS_14" = "false" ] && { log_warn "Not Case 1/4, skipping"; exit 0; }
    is_hardware_supported "$JOB_NAME" || { log_warn "Job not on supported hardware, skipping"; exit 0; }

    # Job duration check (from last successful run)
    LAST_SUCCESS_URL=$(jq -r '(if type == "array" then . else (.runs // []) end) | map(select(.status == "success")) | first | .job_url // ""' "$SUBJOB_RUNS_PATH" 2>/dev/null || echo "")
    JOB_DURATION_SEC=0
    if [ -n "$LAST_SUCCESS_URL" ] && [ "$LAST_SUCCESS_URL" != "null" ]; then
        JID=$(echo "$LAST_SUCCESS_URL" | sed -n 's#.*/job/\([0-9][0-9]*\)#\1#p')
        if [ -n "$JID" ]; then
            JOB_INFO=$(get_job_info "$JID" 2>/dev/null || echo "{}")
            STARTED=$(echo "$JOB_INFO" | jq -r '.started_at // empty')
            ENDED=$(echo "$JOB_INFO" | jq -r '.completed_at // empty')
            if [ -n "$STARTED" ] && [ -n "$ENDED" ]; then
                if command -v gdate &>/dev/null; then
                    END_EPOCH=$(gdate -d "$ENDED" +%s 2>/dev/null || echo 0)
                    START_EPOCH=$(gdate -d "$STARTED" +%s 2>/dev/null || echo 0)
                else
                    END_EPOCH=$(date -d "$ENDED" +%s 2>/dev/null || echo 0)
                    START_EPOCH=$(date -d "$STARTED" +%s 2>/dev/null || echo 0)
                fi
                [ "$END_EPOCH" != "0" ] && [ "$START_EPOCH" != "0" ] && JOB_DURATION_SEC=$((END_EPOCH - START_EPOCH))
            fi
        fi
    fi
    [ "$JOB_DURATION_SEC" -gt "$MAX_DURATION_SECONDS" ] && { log_warn "Job >3h, skipping"; exit 0; }
fi
log_success "Retry conditions met"

# --- Resolve run_id / job_id ---
FAILING_RUN_URL=""
[ -f "$SLACK_MSG_PATH" ] && FAILING_RUN_URL=$(jq -r '.failing_run_url // ""' "$SLACK_MSG_PATH")
[ -z "$FAILING_RUN_URL" ] && [ -f "$SUBJOB_RUNS_PATH" ] && \
    FAILING_RUN_URL=$(jq -r '(if type == "array" then . else (.runs // []) end) | map(select(.status == "failure")) | sort_by(.run_number // 0) | last | .job_url // .run_url // ""' "$SUBJOB_RUNS_PATH" 2>/dev/null || echo "")
if [ -z "$FAILING_RUN_URL" ] || [ "$FAILING_RUN_URL" = "null" ]; then
    log_error "No failing_run_url"
    exit 0
fi

RUN_ID=$(echo "$FAILING_RUN_URL" | sed -n 's#.*/runs/\([0-9][0-9]*\)/job/.*#\1#p')
ORIGINAL_JOB_ID=$(echo "$FAILING_RUN_URL" | sed -n 's#.*/job/\([0-9][0-9]*\)#\1#p')
if [ -z "$RUN_ID" ] || [ -z "$ORIGINAL_JOB_ID" ]; then
    log_error "Could not parse run_id/job_id from URL"
    exit 0
fi
log_info "Run ID: $RUN_ID, Original Job ID: $ORIGINAL_JOB_ID"

# Probe for actual latest attempt (don't probe forever - cap at 10 additional attempts)
RUN_INFO=$(get_run_info "$RUN_ID")
API_ATTEMPT=$(echo "$RUN_INFO" | jq -r '.run_attempt // 1')
OLD_ATTEMPT="$API_ATTEMPT"
for p in 1 2 3 4 5 6 7 8 9 10; do
    PROBE=$((API_ATTEMPT + p))
    JOBS=$(get_jobs_for_run "$RUN_ID" "$PROBE" 2>/dev/null || echo '{"jobs":[]}')
    echo "$JOBS" | jq -e '.jobs | length > 0' >/dev/null 2>&1 || break
    OLD_ATTEMPT="$PROBE"
done

# Resolve JOB_ID in current attempt (may differ if run was retried)
JOB_ID="$ORIGINAL_JOB_ID"
if [ "$OLD_ATTEMPT" -gt 1 ]; then
    FOUND_ID=$(find_job_in_attempt "$RUN_ID" "$OLD_ATTEMPT" "$JOB_NAME")
    if [ -n "$FOUND_ID" ]; then
        JOB_ID="$FOUND_ID"
    else
        ORIG_INFO=$(get_job_info "$ORIGINAL_JOB_ID")
        ORIG_ATTEMPT=$(echo "$ORIG_INFO" | jq -r '.run_attempt // "unknown"')
        if [ "$ORIG_ATTEMPT" != "$OLD_ATTEMPT" ]; then
            log_warn "Cannot re-run jobs from older attempts (${ORIG_ATTEMPT} → ${OLD_ATTEMPT})"
            send_notification "$(printf ':warning: *Auto-retry skipped.*\nRun re-run since failure (attempt %s → %s). Cannot re-run older attempts.' "$ORIG_ATTEMPT" "$OLD_ATTEMPT")"
            exit 0
        fi
    fi
fi
log_info "Using Job ID: $JOB_ID"

# Save original error
ORIGINAL_ERROR=$(jq -r '.failure_message // ""' "$SLACK_MSG_PATH" 2>/dev/null || echo "")
[ -z "$ORIGINAL_ERROR" ] && [ -f "${DATA_DIR}/error_message.txt" ] && ORIGINAL_ERROR=$(cat "${DATA_DIR}/error_message.txt")
[ -z "$ORIGINAL_ERROR" ] && ORIGINAL_ERROR="(error message not available)"
mkdir -p "$DATA_DIR"
echo "$ORIGINAL_ERROR" > "${DATA_DIR}/original_error.txt"

# --- Trigger ---
trigger_retry_run "$JOB_ID" || { send_notification "$(printf ':warning: *Auto-retry failed to trigger.* Job: %s' "$JOB_NAME")"; exit 0; }

# Slack: retry started (predicted new attempt URL)
NEW_ATTEMPT=$((OLD_ATTEMPT + 1))
EARLY_RETRY_URL="${AT_BASE_URL}/actions/runs/${RUN_ID}/attempts/${NEW_ATTEMPT}"
send_notification "$(printf ':arrows_counterclockwise: *Deterministic failure suspected.* Re-running job to confirm:\n<%s|View retry job>\n\n_Workflow:_ %s\n_Job:_ %s' "$EARLY_RETRY_URL" "$WORKFLOW_NAME" "$JOB_NAME")"

# --- Wait ---
STATUS=$(wait_for_run_completion "$RUN_ID" "$JOB_NAME" "$OLD_ATTEMPT" $((MAX_WAIT_MINUTES * 60)) 60) || true
RETRY_JOB_ID=$(find_job_in_attempt "$RUN_ID" "$NEW_ATTEMPT" "$JOB_NAME")
if [ -z "$RETRY_JOB_ID" ]; then
    log_warn "Retry job '${JOB_NAME}' not found in attempt ${NEW_ATTEMPT} for run ${RUN_ID}"
    RETRY_JOB_URL="$EARLY_RETRY_URL"
else
    RETRY_JOB_URL="${AT_BASE_URL}/actions/runs/${RUN_ID}/job/${RETRY_JOB_ID}"
fi

if [ "$STATUS" = "timeout" ] || [ "$STATUS" = "error" ]; then
    send_notification "$(printf ':hourglass: *Retry timed out.* Job did not complete within %d min. Proceeding with original analysis.\n<%s|Check retry>' "$MAX_WAIT_MINUTES" "$EARLY_RETRY_URL")"
    if [ -f "$SLACK_MSG_PATH" ]; then
        EXISTING=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
        ADD="*NOTE:* Automatic retry timed out. Analysis based on original failure only."
        if [ -n "$EXISTING" ] && [ "$EXISTING" != "null" ]; then
            COMBINED="${EXISTING}

${ADD}"
        else
            COMBINED="$ADD"
        fi
        jq --arg notes "$COMBINED" '.notes = $notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp" && mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    fi
    exit 0
fi

if [ "$STATUS" = "success" ]; then
    jq -n --arg r "passed" --arg m "Retry passed" '{result: $r, message: $m}' > "$RETRY_RESULT_FILE"
    RETRY_NOTE="*RETRY PASSED - CONVERTED TO CASE 3:* Failure passed on retry (non-deterministic). Original: ${FAILING_RUN_URL} Retry: ${RETRY_JOB_URL}"
    EXISTING=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
    if [ -n "$EXISTING" ] && [ "$EXISTING" != "null" ]; then
        COMBINED="${EXISTING}

----

${RETRY_NOTE}"
    else
        COMBINED="$RETRY_NOTE"
    fi
    jq --arg scenario "Failure likely outside tt-metal" --arg case "3" --arg notes "$COMBINED" --arg slack "Failure is non-deterministic. Passed on retry." \
        '. + {scenario: $scenario, case: $case, notes: $notes, slack_message: $slack, commits: []}' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp" && mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    printf '# Auto Triage: %s\n## Non-Deterministic (Passed on Retry)\nOriginal: %s\nRetry: %s\n----\n_Automatic analysis._\n' "$JOB_NAME" "$FAILING_RUN_URL" "$RETRY_JOB_URL" > "$EXPLANATION_PATH"
    send_notification "$(printf ':white_check_mark: *Retry passed!* Non-deterministic.\nOriginal: <%s|link> Retry: <%s|link>' "$FAILING_RUN_URL" "$RETRY_JOB_URL")"
    exit 0
fi

if [ "$STATUS" = "cancelled" ]; then
    jq -n --arg r "cancelled" --arg m "Retry cancelled" '{result: $r, message: $m}' > "$RETRY_RESULT_FILE"
    EXISTING=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
    ADD="*NOTE:* Automatic retry was cancelled. Retry link: ${RETRY_JOB_URL}"
    if [ -n "$EXISTING" ] && [ "$EXISTING" != "null" ]; then
        COMBINED="${EXISTING}

${ADD}"
    else
        COMBINED="$ADD"
    fi
    jq --arg notes "$COMBINED" '.notes = $notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp" && mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    send_notification "$(printf ':no_entry_sign: *Retry cancelled.* Original: <%s|link>' "$FAILING_RUN_URL")"
    exit 0
fi

# --- Unknown status ---
# basically it didn't fail, but we don't have the information to figure out what happened, so we can't proceed.
if [ "$STATUS" != "failure" ]; then
    jq -n --arg r "unknown" --arg m "Unexpected status: $STATUS" --arg c "$STATUS" '{result: $r, message: $m, conclusion: $c}' > "$RETRY_RESULT_FILE"
    EXISTING=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
    ADD="*NOTE:* Retry ended with status: ${STATUS}. Retry link: ${RETRY_JOB_URL}"
    if [ -n "$EXISTING" ] && [ "$EXISTING" != "null" ]; then
        COMBINED="${EXISTING}

${ADD}"
    else
        COMBINED="$ADD"
    fi
    jq --arg notes "$COMBINED" '.notes = $notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp" && mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    send_notification "$(printf ':grey_question: *Retry ended: %s* Original: <%s|link>' "$STATUS" "$FAILING_RUN_URL")"
    exit 0
fi

# --- STATUS = failure: compare errors ---
mkdir -p "${LOGS_DIR}/retry_job_${RETRY_JOB_ID}"
"${ROOT}/get_annotations.sh" "$RETRY_JOB_URL" "${LOGS_DIR}/retry_job_${RETRY_JOB_ID}/annotations.json" 2>/dev/null || true
"${ROOT}/get_logs.sh" "$RETRY_JOB_URL" "${LOGS_DIR}/retry" 2>/dev/null || true
RETRY_ERROR=""
LOG_DIR="${LOGS_DIR}/retry/job_${RETRY_JOB_ID}"
[ -d "$LOG_DIR" ] && RETRY_ERROR=$(find "$LOG_DIR" -name "*.txt" -exec grep -h -m 50 -A10 -E "(FAILED|ERROR:|Exception:|AssertionError|pytest.*failed)" {} \; 2>/dev/null)
[ -z "$RETRY_ERROR" ] && [ -f "${LOGS_DIR}/retry_job_${RETRY_JOB_ID}/annotations.json" ] && \
    RETRY_ERROR=$(jq -r '[.[] | select((.annotation_level | ascii_downcase) == "failure")] | map(.message // "") | join("\n")' "${LOGS_DIR}/retry_job_${RETRY_JOB_ID}/annotations.json" 2>/dev/null || echo "")
if [ -z "$RETRY_ERROR" ]; then
    log_warn "No error extracted from logs or annotations for retry job ${RETRY_JOB_ID}"
    RETRY_ERROR="Could not extract error from retry job"
fi
echo "$RETRY_ERROR" > "${DATA_DIR}/retry_error.txt"

run_copilot_error_comparison "$ROOT" "$DATA_DIR" || true
SAME_FAILURE=$(get_same_failure_from_comparison "$COMPARISON_FILE")
RESULT=$(determine_retry_result "$STATUS" "$SAME_FAILURE")

RETRY_ERR_FOR_NOTES=$(get_retry_error_extracted "$COMPARISON_FILE")
[ -z "$RETRY_ERR_FOR_NOTES" ] && RETRY_ERR_FOR_NOTES=$(echo "$RETRY_ERROR" | head -c 500)

if [ "$RESULT" = "failed_same" ]; then
    jq -n --arg r "failed_same" --arg m "Same error, deterministic confirmed" '{result: $r, message: $m}' > "$RETRY_RESULT_FILE"
    RETRY_NOTE="*RETRY CONFIRMED DETERMINISTIC:* Same error on retry. ${RETRY_JOB_URL}"
    EXISTING=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
    if [ -n "$EXISTING" ] && [ "$EXISTING" != "null" ]; then
        COMBINED="${EXISTING}

----

${RETRY_NOTE}"
    else
        COMBINED="$RETRY_NOTE"
    fi
    jq --arg notes "$COMBINED" '.notes = $notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp" && mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    EXISTING_EXPLANATION=$(cat "$EXPLANATION_PATH" 2>/dev/null || echo "")
    cat > "$EXPLANATION_PATH" << EOF
## Failure Was Repeatable (Deterministic)
Retry failed with same error.
First: $FAILING_RUN_URL
Retry: $RETRY_JOB_URL
----
${EXISTING_EXPLANATION}
EOF
    send_notification "$(printf ':x: *Retry failed with same error.* Deterministic confirmed.\n<%s|link> <%s|link>' "$FAILING_RUN_URL" "$RETRY_JOB_URL")"
else
    jq -n --arg r "failed_different" --arg m "Different error" --arg url "$RETRY_JOB_URL" --arg err "$RETRY_ERR_FOR_NOTES" '{result: $r, message: $m, retry_url: $url, retry_error: $err}' > "$RETRY_RESULT_FILE"
    RETRY_NOTE="*RETRY DIFFERENT ERROR - CASE 3:* Retry failed with different error.

\`\`\`
${RETRY_ERR_FOR_NOTES}
\`\`\`
${RETRY_JOB_URL}"
    EXISTING=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
    if [ -n "$EXISTING" ] && [ "$EXISTING" != "null" ]; then
        COMBINED="${EXISTING}

----

${RETRY_NOTE}"
    else
        COMBINED="$RETRY_NOTE"
    fi
    jq --arg scenario "Failure likely outside tt-metal" --arg case "3" --arg notes "$COMBINED" --arg slack "Non-deterministic. Different errors on retry." \
        '. + {scenario: $scenario, case: $case, notes: $notes, slack_message: $slack, commits: []}' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp" && mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    printf '# Auto Triage: %s\n## Non-Deterministic (Different Errors)\nOriginal: %s\nRetry: %s\n----\n_Automatic analysis._\n' "$JOB_NAME" "$FAILING_RUN_URL" "$RETRY_JOB_URL" > "$EXPLANATION_PATH"
    send_notification "$(printf ':warning: *Retry failed with DIFFERENT error.* Non-deterministic.\n<%s|link> <%s|link>' "$FAILING_RUN_URL" "$RETRY_JOB_URL")"
fi
log_success "Retry logic completed"

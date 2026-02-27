#!/bin/bash
#
# Retry logic for deterministic failures on supported hardware.
# This script checks if a retry should be attempted, runs the retry,
# and handles the outcomes (pass, fail-same, fail-different).
#
# Usage:
#   ./retry_on_deterministic.sh <job_name> <workflow_name>
#
# Environment variables required:
#   SLACK_BOT_TOKEN - Slack bot token for sending notifications
#   SLACK_CHANNEL_ID - Slack channel ID to post to
#   GH_TOKEN - GitHub token for API calls
#   GITHUB_TOKEN - GitHub token (fallback)
#   COPILOT_GITHUB_TOKEN - Token for Copilot CLI
#
# Outputs:
#   Sets RETRY_RESULT to one of: "no_retry", "passed", "failed_same", "failed_different"
#   Modifies slack_message.json and explanation.md as needed

set -euo pipefail

# ============================================================================
# TESTING MODE FLAG
# Set to "true" to force retry regardless of case/hardware (for testing only)
# Set to "false" for normal production behavior
# ============================================================================
TEST_MODE="false"
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ $# -lt 2 ]; then
    echo -e "${RED}Usage: $0 <job_name> <workflow_name>${NC}" >&2
    exit 1
fi

JOB_NAME="$1"
WORKFLOW_NAME="$2"
SLACK_TS="${3:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Use symlinked paths for consistency with other scripts
# The symlinks are: ./data -> ./auto_triage/data, ./output -> ./auto_triage/output
DATA_DIR="${ROOT}/data"
OUTPUT_DIR="${ROOT}/output"
LOGS_DIR="${ROOT}/logs"

# Fallback to canonical paths if symlinks don't exist
if [ ! -d "$DATA_DIR" ]; then
    DATA_DIR="${ROOT}/auto_triage/data"
fi
if [ ! -d "$OUTPUT_DIR" ]; then
    OUTPUT_DIR="${ROOT}/auto_triage/output"
fi
if [ ! -d "$LOGS_DIR" ]; then
    LOGS_DIR="${ROOT}/auto_triage/logs"
fi

SLACK_MSG_PATH="${OUTPUT_DIR}/slack_message.json"
EXPLANATION_PATH="${OUTPUT_DIR}/explanation.md"
SUBJOB_RUNS_PATH="${DATA_DIR}/subjob_runs.json"

# Output file to signal retry result to calling script
RETRY_RESULT_FILE="${DATA_DIR}/retry_result.json"

echo -e "${BLUE}Retry script paths:${NC}"
echo -e "  ROOT: ${ROOT}"
echo -e "  DATA_DIR: ${DATA_DIR}"
echo -e "  OUTPUT_DIR: ${OUTPUT_DIR}"
echo -e "  SLACK_MSG_PATH: ${SLACK_MSG_PATH}"
echo -e "  EXPLANATION_PATH: ${EXPLANATION_PATH}"

OWNER="tenstorrent"
REPO="tt-metal"

# ============================================================================
# Send Slack notification helper function
# Defined early so it can be used for error notifications
# ============================================================================
send_retry_notification() {
    local message="$1"
    local payload
    
    if [ -n "$SLACK_TS" ]; then
        payload=$(jq -n --arg text "$message" --arg ts "$SLACK_TS" '{text: $text, thread_ts: $ts}')
    else
        payload=$(jq -n --arg text "$message" '{text: $text}')
    fi
    
    if [ -n "${SLACK_BOT_TOKEN:-}" ] && [ -n "${SLACK_CHANNEL_ID:-}" ]; then
        echo -e "${BLUE}Sending Slack notification...${NC}"
        SLACK_RESPONSE=$(curl -s -X POST "https://slack.com/api/chat.postMessage" \
            -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "$(echo "$payload" | jq --arg channel "$SLACK_CHANNEL_ID" '. + {channel: $channel}')" \
            2>&1)
        
        SLACK_OK=$(echo "$SLACK_RESPONSE" | jq -r '.ok // false' 2>/dev/null || echo "false")
        if [ "$SLACK_OK" = "true" ]; then
            echo -e "${GREEN}Slack notification sent successfully${NC}"
        else
            SLACK_ERROR=$(echo "$SLACK_RESPONSE" | jq -r '.error // "unknown"' 2>/dev/null || echo "unknown")
            echo -e "${YELLOW}Warning: Slack notification failed: ${SLACK_ERROR}${NC}"
        fi
    else
        echo -e "${YELLOW}Slack credentials not set, skipping notification${NC}"
    fi
}

# Initialize retry result
echo '{"result": "no_retry", "message": ""}' > "$RETRY_RESULT_FILE"

# Check if slack_message.json exists
if [ ! -f "$SLACK_MSG_PATH" ]; then
    if [ "$TEST_MODE" = "true" ]; then
        echo -e "${YELLOW}========================================${NC}"
        echo -e "${YELLOW}TEST MODE: No slack_message.json found, but will try to get job info from subjob_runs.json${NC}"
        echo -e "${YELLOW}========================================${NC}"
        SCENARIO="(cancelled/no analysis)"
    else
        echo -e "${YELLOW}No slack_message.json found, skipping retry logic${NC}"
        exit 0
    fi
else
    # Read the scenario field
    SCENARIO=$(jq -r '.scenario // ""' "$SLACK_MSG_PATH")
fi

echo -e "${BLUE}Scenario: ${SCENARIO}${NC}"

# TEST_MODE: Skip all eligibility checks
if [ "$TEST_MODE" = "true" ]; then
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}TEST MODE ENABLED - Forcing retry regardless of case/hardware${NC}"
    echo -e "${YELLOW}========================================${NC}"
else
    # Check if this is a Case 1 or Case 4 (deterministic failure with commits)
    # Use flexible matching: must contain both "deterministic" and "commit"
    # but must NOT contain "non-deterministic" or "non deterministic"
    # This handles variations in LLM output like "deterministic_commit_identified"
    SCENARIO_LOWER=$(echo "$SCENARIO" | tr '[:upper:]' '[:lower:]')
    
    # Check for exclusion patterns first (non-deterministic)
    if [[ "$SCENARIO_LOWER" == *"non-deterministic"* ]] || \
       [[ "$SCENARIO_LOWER" == *"non deterministic"* ]] || \
       [[ "$SCENARIO_LOWER" == *"outside tt-metal"* ]] || \
       [[ "$SCENARIO_LOWER" == *"case 3"* ]]; then
        echo -e "${YELLOW}Not a Case 1/4 scenario (non-deterministic/Case 3), skipping retry${NC}"
        echo -e "${YELLOW}(Scenario was: ${SCENARIO})${NC}"
        exit 0
    fi
    
    # Check for inclusion - multiple ways to detect Case 1/4:
    # 1. Scenario contains "deterministic" or "culprit" or "identified"
    # 2. OR the slack_message has commits with confidence scores
    IS_CASE_1_OR_4="false"
    
    # Method 1: Check scenario string
    if [[ "$SCENARIO_LOWER" == *"deterministic"* ]] || \
       [[ "$SCENARIO_LOWER" == *"culprit"* ]] || \
       [[ "$SCENARIO_LOWER" == *"identified"* ]] || \
       [[ "$SCENARIO_LOWER" == *"case 1"* ]] || \
       [[ "$SCENARIO_LOWER" == *"case 4"* ]]; then
        echo -e "${GREEN}Detected Case 1/4 from scenario string${NC}"
        IS_CASE_1_OR_4="true"
    fi
    
    # Method 2: Check if there are commits with confidence scores in slack_message.json
    if [ "$IS_CASE_1_OR_4" = "false" ] && [ -f "$SLACK_MSG_PATH" ]; then
        HAS_CONFIDENCE=$(jq -r '.commits // [] | map(select(.confidence != null and .confidence > 0)) | length' "$SLACK_MSG_PATH" 2>/dev/null || echo "0")
        if [ "$HAS_CONFIDENCE" -gt 0 ]; then
            echo -e "${GREEN}Detected Case 1/4 from commits with confidence scores (found ${HAS_CONFIDENCE} commits)${NC}"
            IS_CASE_1_OR_4="true"
        fi
    fi
    
    if [ "$IS_CASE_1_OR_4" = "false" ]; then
        echo -e "${YELLOW}Not a Case 1/4 scenario, skipping retry${NC}"
        echo -e "${YELLOW}(Scenario was: ${SCENARIO})${NC}"
        exit 0
    fi

    # Check if job name contains supported hardware (N150, N300, P150, P300)
    # Case-insensitive check
    JOB_NAME_LOWER=$(echo "$JOB_NAME" | tr '[:upper:]' '[:lower:]')
    if ! echo "$JOB_NAME_LOWER" | grep -qiE '(n150|n300|p150|p300|p100)'; then
        echo -e "${YELLOW}Job '$JOB_NAME' does not contain N150/N300/P150/P300/P100A, skipping retry${NC}"
        echo -e "${YELLOW}(Jobs with galaxy, T3K, or are too expensive for automatic retries)${NC}"
        exit 0
    fi

    # Check for expensive hardware that should NOT be retried
    if echo "$JOB_NAME_LOWER" | grep -qiE '(galaxy|t3k|t3000)'; then
        echo -e "${YELLOW}Job '$JOB_NAME' contains expensive hardware (galaxy/T3K), skipping retry${NC}"
        exit 0
    fi
fi

# ============================================================================
# Check if the job typically takes more than 3 hours (skip retry if so)
# ============================================================================
MAX_DURATION_SECONDS=$((3 * 60 * 60))  # 3 hours in seconds

echo -e "${BLUE}Checking job duration from last successful run...${NC}"

# Get the last successful job URL from subjob_runs.json (already collected by find_boundaries.sh)
LAST_SUCCESS_JOB_URL=""
if [ -f "$SUBJOB_RUNS_PATH" ]; then
    LAST_SUCCESS_JOB_URL=$(jq -r '
        (if type == "array" then . else (.runs // []) end) |
        map(select(.status == "success")) |
        first |
        .job_url // ""
    ' "$SUBJOB_RUNS_PATH" 2>/dev/null || echo "")
fi

FOUND_DURATION="false"
JOB_DURATION_SECONDS=0

if [ -n "$LAST_SUCCESS_JOB_URL" ] && [ "$LAST_SUCCESS_JOB_URL" != "null" ]; then
    echo -e "${BLUE}Found last successful job: ${LAST_SUCCESS_JOB_URL}${NC}"
    
    # Parse job ID from URL: https://github.com/owner/repo/actions/runs/RUN_ID/job/JOB_ID
    SUCCESS_JOB_ID=$(echo "$LAST_SUCCESS_JOB_URL" | sed -n 's#.*/job/\([0-9]\+\).*#\1#p')
    
    if [ -n "$SUCCESS_JOB_ID" ]; then
        # Query this specific job to get timing info
        JOB_INFO=$(gh api "repos/${OWNER}/${REPO}/actions/jobs/${SUCCESS_JOB_ID}" 2>/dev/null || echo "{}")
        
        STARTED_AT=$(echo "$JOB_INFO" | jq -r '.started_at // empty' 2>/dev/null)
        COMPLETED_AT=$(echo "$JOB_INFO" | jq -r '.completed_at // empty' 2>/dev/null)
        
        if [ -n "$STARTED_AT" ] && [ -n "$COMPLETED_AT" ] && [ "$STARTED_AT" != "null" ] && [ "$COMPLETED_AT" != "null" ]; then
            # Convert to epoch timestamps and calculate difference
            if command -v gdate &> /dev/null; then
                # macOS with coreutils
                START_EPOCH=$(gdate -d "$STARTED_AT" +%s 2>/dev/null || echo "0")
                END_EPOCH=$(gdate -d "$COMPLETED_AT" +%s 2>/dev/null || echo "0")
            else
                # Linux
                START_EPOCH=$(date -d "$STARTED_AT" +%s 2>/dev/null || echo "0")
                END_EPOCH=$(date -d "$COMPLETED_AT" +%s 2>/dev/null || echo "0")
            fi
            
            if [ "$START_EPOCH" != "0" ] && [ "$END_EPOCH" != "0" ]; then
                JOB_DURATION_SECONDS=$((END_EPOCH - START_EPOCH))
                FOUND_DURATION="true"
                DURATION_HOURS=$((JOB_DURATION_SECONDS / 3600))
                DURATION_MINS=$(((JOB_DURATION_SECONDS % 3600) / 60))
                echo -e "${BLUE}Last successful run took: ${DURATION_HOURS}h ${DURATION_MINS}m${NC}"
            fi
        fi
    fi
else
    echo -e "${YELLOW}No successful job URL found in subjob_runs.json${NC}"
fi

if [ "$FOUND_DURATION" = "true" ] && [ "$JOB_DURATION_SECONDS" -gt "$MAX_DURATION_SECONDS" ]; then
    DURATION_HOURS=$((JOB_DURATION_SECONDS / 3600))
    DURATION_MINS=$(((JOB_DURATION_SECONDS % 3600) / 60))
    echo -e "${YELLOW}Job takes ${DURATION_HOURS}h ${DURATION_MINS}m (>3h), skipping retry to save resources${NC}"
    exit 0
elif [ "$FOUND_DURATION" = "false" ]; then
    echo -e "${YELLOW}Could not determine job duration, proceeding with retry${NC}"
fi

echo -e "${GREEN}Retry conditions met: proceeding with retry${NC}"

# Get the failing job URL and extract IDs
# First try slack_message.json, then fall back to subjob_runs.json
FAILING_RUN_URL=""
if [ -f "$SLACK_MSG_PATH" ]; then
    FAILING_RUN_URL=$(jq -r '.failing_run_url // ""' "$SLACK_MSG_PATH")
fi

# If no URL from slack_message.json, try subjob_runs.json (for cancelled runs or TEST_MODE)
if [ -z "$FAILING_RUN_URL" ] && [ -f "$SUBJOB_RUNS_PATH" ]; then
    echo -e "${BLUE}Getting failing job URL from subjob_runs.json...${NC}"
    # Get the most recent failure (highest run_number with status "failure")
    FAILING_RUN_URL=$(jq -r '
        (if type == "array" then . else (.runs // []) end) |
        map(select(.status == "failure")) |
        sort_by(.run_number // 0) |
        last |
        .job_url // .run_url // ""
    ' "$SUBJOB_RUNS_PATH" 2>/dev/null || echo "")
    
    if [ -n "$FAILING_RUN_URL" ] && [ "$FAILING_RUN_URL" != "null" ]; then
        echo -e "${GREEN}Found failing job URL from subjob_runs.json: ${FAILING_RUN_URL}${NC}"
    fi
fi

if [ -z "$FAILING_RUN_URL" ] || [ "$FAILING_RUN_URL" = "null" ]; then
    echo -e "${RED}No failing_run_url found in slack_message.json or subjob_runs.json${NC}"
    exit 0
fi

# Parse run ID and job ID from URL
# Format: https://github.com/tenstorrent/tt-metal/actions/runs/RUN_ID/job/JOB_ID
RUN_ID=$(echo "$FAILING_RUN_URL" | sed -n 's#.*/runs/\([0-9]\+\)/job/.*#\1#p')
ORIGINAL_JOB_ID=$(echo "$FAILING_RUN_URL" | sed -n 's#.*/job/\([0-9]\+\).*#\1#p')

if [ -z "$RUN_ID" ] || [ -z "$ORIGINAL_JOB_ID" ]; then
    echo -e "${RED}Could not parse run_id/job_id from URL: $FAILING_RUN_URL${NC}"
    exit 0
fi

echo -e "${BLUE}Run ID: $RUN_ID, Original Job ID: $ORIGINAL_JOB_ID${NC}"
echo -e "${BLUE}Failing run URL from slack_message: ${FAILING_RUN_URL}${NC}"

# Get the current run_attempt BEFORE triggering rerun
# NOTE: The API's run_attempt may be stale if only specific jobs were re-run
RUN_INFO_BEFORE=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}" 2>/dev/null || echo "{}")
API_ATTEMPT=$(echo "$RUN_INFO_BEFORE" | jq -r '.run_attempt // 1')
RUN_STATUS=$(echo "$RUN_INFO_BEFORE" | jq -r '.status // "unknown"')
RUN_CONCLUSION=$(echo "$RUN_INFO_BEFORE" | jq -r '.conclusion // "unknown"')
echo -e "${BLUE}API reports run_attempt: ${API_ATTEMPT} (status: ${RUN_STATUS}, conclusion: ${RUN_CONCLUSION})${NC}"

# The API's run_attempt can be stale when only specific jobs are re-run
# Find the ACTUAL latest attempt by probing higher attempt numbers
echo -e "${BLUE}Probing for higher attempt numbers...${NC}"
OLD_ATTEMPT="$API_ATTEMPT"
PROBE_ATTEMPT=$((API_ATTEMPT + 1))
MAX_PROBE=10  # Don't probe forever

while [ $PROBE_ATTEMPT -le $((API_ATTEMPT + MAX_PROBE)) ]; do
    PROBE_RESULT=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}/attempts/${PROBE_ATTEMPT}/jobs?per_page=1" 2>/dev/null || echo "NOT_FOUND")
    
    if echo "$PROBE_RESULT" | jq -e '.jobs | length > 0' >/dev/null 2>&1; then
        echo -e "${GREEN}Found attempt ${PROBE_ATTEMPT} exists!${NC}"
        OLD_ATTEMPT="$PROBE_ATTEMPT"
        PROBE_ATTEMPT=$((PROBE_ATTEMPT + 1))
    else
        # No more attempts
        break
    fi
done

if [ "$OLD_ATTEMPT" != "$API_ATTEMPT" ]; then
    echo -e "${GREEN}Actual latest attempt: ${OLD_ATTEMPT} (API reported ${API_ATTEMPT})${NC}"
else
    echo -e "${BLUE}Confirmed latest attempt: ${OLD_ATTEMPT}${NC}"
fi

# Also check the original job's attempt to understand the situation
ORIG_JOB_CHECK=$(gh api "repos/${OWNER}/${REPO}/actions/jobs/${ORIGINAL_JOB_ID}" 2>/dev/null || echo "{}")
ORIG_JOB_RUN_ATTEMPT=$(echo "$ORIG_JOB_CHECK" | jq -r '.run_attempt // "unknown"')
echo -e "${BLUE}Original job ${ORIGINAL_JOB_ID} is from run_attempt: ${ORIG_JOB_RUN_ATTEMPT}${NC}"

# ============================================================================
# Find the job ID from the CURRENT attempt (GitHub only allows re-running
# jobs from the current attempt, not older attempts)
# ============================================================================
JOB_ID="$ORIGINAL_JOB_ID"

if [ "$OLD_ATTEMPT" -gt 1 ]; then
    echo -e "${BLUE}Run has been retried before (attempt ${OLD_ATTEMPT}), finding job in current attempt...${NC}"
    
    # Get jobs from the current attempt
    CURRENT_JOBS=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}/attempts/${OLD_ATTEMPT}/jobs?per_page=100" 2>/dev/null || echo '{"jobs":[]}')
    
    # Debug: Show how many jobs we found and list them
    JOBS_COUNT=$(echo "$CURRENT_JOBS" | jq '.jobs | length' 2>/dev/null || echo "0")
    echo -e "${BLUE}Found ${JOBS_COUNT} jobs in attempt ${OLD_ATTEMPT}${NC}"
    
    # Find the job matching our job name (case-insensitive, handle unicode dashes)
    JOB_NAME_LOWER=$(echo "$JOB_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[–—−‐‑‒]/-/g')
    echo -e "${BLUE}Looking for job name (normalized): ${JOB_NAME_LOWER}${NC}"
    
    # Debug: Show all job names in this attempt
    echo -e "${BLUE}Jobs in attempt ${OLD_ATTEMPT}:${NC}"
    echo "$CURRENT_JOBS" | jq -r '.jobs // [] | .[].name' 2>/dev/null | head -20 || echo "(none)"
    
    CURRENT_JOB_ID=$(echo "$CURRENT_JOBS" | jq -r --arg name "$JOB_NAME_LOWER" '
        def normalize: ascii_downcase | gsub("[–—−‐‑‒]"; "-");
        .jobs // [] | 
        map(select((.name | normalize) == $name or (.name | normalize | contains($name)) or ($name | contains(.name | normalize)))) | 
        first | 
        .id // empty
    ' 2>/dev/null || echo "")
    
    if [ -n "$CURRENT_JOB_ID" ] && [ "$CURRENT_JOB_ID" != "null" ]; then
        echo -e "${GREEN}Found job in current attempt: ${CURRENT_JOB_ID}${NC}"
        JOB_ID="$CURRENT_JOB_ID"
    else
        echo -e "${YELLOW}Could not find job in current attempt by name${NC}"
        
        # Check what attempt the original job is from
        ORIG_JOB_INFO=$(gh api "repos/${OWNER}/${REPO}/actions/jobs/${ORIGINAL_JOB_ID}" 2>/dev/null || echo "{}")
        ORIG_JOB_ATTEMPT=$(echo "$ORIG_JOB_INFO" | jq -r '.run_attempt // "unknown"')
        ORIG_JOB_NAME=$(echo "$ORIG_JOB_INFO" | jq -r '.name // "unknown"')
        echo -e "${YELLOW}Original job ${ORIGINAL_JOB_ID} is from attempt ${ORIG_JOB_ATTEMPT}, name: ${ORIG_JOB_NAME}${NC}"
        
        # If original job is from an older attempt, we cannot re-run it
        if [ "$ORIG_JOB_ATTEMPT" != "$OLD_ATTEMPT" ]; then
            echo -e "${YELLOW}Original job is from attempt ${ORIG_JOB_ATTEMPT} but current attempt is ${OLD_ATTEMPT}${NC}"
            echo -e "${YELLOW}Cannot re-run jobs from older attempts. Skipping retry.${NC}"
            send_retry_notification "$(printf ':warning: *Auto-retry skipped.*\n\nJob: %s\nWorkflow: %s\nOriginal failure: <%s|link>\n\n_The run has been re-run since the original failure (attempt %s → %s). Cannot re-run jobs from older attempts._\n\n_Proceeding with original analysis._' "$JOB_NAME" "$WORKFLOW_NAME" "$FAILING_RUN_URL" "$ORIG_JOB_ATTEMPT" "$OLD_ATTEMPT")"
            exit 0
        fi
    fi
fi

echo -e "${BLUE}Using Job ID: $JOB_ID${NC}"

# Save the original error message for comparison
ORIGINAL_ERROR=""
if [ -f "$SLACK_MSG_PATH" ]; then
    ORIGINAL_ERROR=$(jq -r '.failure_message // ""' "$SLACK_MSG_PATH")
fi
# Also try error_message.txt from filter stage if no error in slack_message.json
if [ -z "$ORIGINAL_ERROR" ] && [ -f "${DATA_DIR}/error_message.txt" ]; then
    ORIGINAL_ERROR=$(cat "${DATA_DIR}/error_message.txt" 2>/dev/null || echo "")
    echo -e "${BLUE}Got error message from error_message.txt${NC}"
fi
if [ -z "$ORIGINAL_ERROR" ]; then
    ORIGINAL_ERROR="(error message not available)"
fi
mkdir -p "$DATA_DIR"
echo "$ORIGINAL_ERROR" > "${DATA_DIR}/original_error.txt"

# Re-run the specific failed job (not all failed jobs)
echo -e "${GREEN}Re-running specific job ${JOB_ID}...${NC}"

# GitHub API to re-run a SPECIFIC job (not all failed jobs)
# POST /repos/{owner}/{repo}/actions/jobs/{job_id}/rerun
# NOTE: This API returns 201 with empty body on success
# NOTE: Requires 'actions: write' permission on GITHUB_TOKEN
echo -e "${BLUE}Using API: repos/${OWNER}/${REPO}/actions/jobs/${JOB_ID}/rerun${NC}"
RERUN_RESPONSE=$(gh api \
    --method POST \
    "repos/${OWNER}/${REPO}/actions/jobs/${JOB_ID}/rerun" \
    -i 2>&1 || echo "API_ERROR")

# Extract HTTP status code from response headers
RERUN_HTTP_CODE=$(echo "$RERUN_RESPONSE" | head -1 | awk '{print $2}')
RERUN_HTTP_CODE="${RERUN_HTTP_CODE:-000}"

echo -e "${BLUE}Rerun job API response code: ${RERUN_HTTP_CODE}${NC}"

if [ "$RERUN_HTTP_CODE" != "201" ] && [ "$RERUN_HTTP_CODE" != "200" ]; then
    # Show error details
    echo -e "${RED}Failed to re-run specific job (HTTP ${RERUN_HTTP_CODE})${NC}"
    ERROR_MSG=$(echo "$RERUN_RESPONSE" | grep -A5 '"message"' | head -3 || echo "")
    if [ -n "$ERROR_MSG" ]; then
        echo -e "${RED}Error details: ${ERROR_MSG}${NC}"
    fi
    
    if [ "$RERUN_HTTP_CODE" = "403" ]; then
        echo -e "${YELLOW}NOTE: 403 Forbidden usually means the GITHUB_TOKEN needs 'actions: write' permission${NC}"
        echo -e "${YELLOW}Add 'permissions: actions: write' to your workflow file${NC}"
    fi
    
    # Don't fallback to rerun-failed-jobs - just proceed with original analysis
    echo -e "${YELLOW}Proceeding without retry${NC}"
    send_retry_notification "$(printf ':warning: *Auto-retry failed to trigger.*\n\nJob: %s\nWorkflow: %s\nHTTP code: %s\n\n_Proceeding with original analysis._' "$JOB_NAME" "$WORKFLOW_NAME" "$RERUN_HTTP_CODE")"
    exit 0
fi

echo -e "${GREEN}Re-run triggered successfully${NC}"

# Wait for the new attempt to be created and become visible
echo -e "${BLUE}Waiting for new run attempt to start...${NC}"
NEW_ATTEMPT="$OLD_ATTEMPT"
WAIT_FOR_START=0
MAX_WAIT_FOR_START=120  # 2 minutes max to wait for new attempt to appear

while [ "$NEW_ATTEMPT" = "$OLD_ATTEMPT" ] && [ $WAIT_FOR_START -lt $MAX_WAIT_FOR_START ]; do
    sleep 10
    WAIT_FOR_START=$((WAIT_FOR_START + 10))
    RUN_INFO=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}" 2>/dev/null || echo "{}")
    NEW_ATTEMPT=$(echo "$RUN_INFO" | jq -r '.run_attempt // 1')
    RUN_STATUS=$(echo "$RUN_INFO" | jq -r '.status // "unknown"')
    echo -e "${BLUE}  Waited ${WAIT_FOR_START}s - run_attempt: ${NEW_ATTEMPT}, status: ${RUN_STATUS}${NC}"
done

if [ "$NEW_ATTEMPT" = "$OLD_ATTEMPT" ]; then
    echo -e "${RED}New run attempt did not start within ${MAX_WAIT_FOR_START}s${NC}"
    echo -e "${YELLOW}Proceeding without retry${NC}"
    send_retry_notification "$(printf ':warning: *Auto-retry failed to start.*\n\nJob: %s\nWorkflow: %s\n\n_New run attempt did not start within %ds. Proceeding with original analysis._' "$JOB_NAME" "$WORKFLOW_NAME" "$MAX_WAIT_FOR_START")"
    exit 0
fi

NEW_RUN_URL="https://github.com/${OWNER}/${REPO}/actions/runs/${RUN_ID}/attempts/${NEW_ATTEMPT}"

echo -e "${GREEN}New run attempt started: ${NEW_ATTEMPT}${NC}"
echo -e "${GREEN}Retry run URL: ${NEW_RUN_URL}${NC}"

# Try to find the specific job ID in the new attempt for a more precise link
echo -e "${BLUE}Finding specific job in new attempt...${NC}"
EARLY_RETRY_JOB_URL="$NEW_RUN_URL"  # Default to run URL if we can't find job

# Wait a moment for the job to be created
sleep 5

# Try to find the job matching our job name
EARLY_JOBS=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}/attempts/${NEW_ATTEMPT}/jobs?per_page=100" 2>/dev/null || echo "{}")
EARLY_JOBS_COUNT=$(echo "$EARLY_JOBS" | jq '.jobs | length' 2>/dev/null || echo "0")

if [ "$EARLY_JOBS_COUNT" != "0" ]; then
    # Try to find our specific job
    JOB_NAME_LOWER=$(echo "$JOB_NAME" | tr '[:upper:]' '[:lower:]')
    EARLY_JOB_ID=$(echo "$EARLY_JOBS" | jq -r --arg name "$JOB_NAME_LOWER" '
        .jobs // [] |
        map(select(.name | ascii_downcase | contains($name))) |
        first | .id // empty
    ' 2>/dev/null || echo "")
    
    if [ -n "$EARLY_JOB_ID" ] && [ "$EARLY_JOB_ID" != "null" ]; then
        EARLY_RETRY_JOB_URL="https://github.com/${OWNER}/${REPO}/actions/runs/${RUN_ID}/job/${EARLY_JOB_ID}"
        echo -e "${GREEN}Found specific job ID: ${EARLY_JOB_ID}${NC}"
        echo -e "${GREEN}Job URL: ${EARLY_RETRY_JOB_URL}${NC}"
    else
        echo -e "${YELLOW}Could not find specific job by name, using run URL${NC}"
    fi
else
    echo -e "${YELLOW}No jobs found yet in new attempt, using run URL${NC}"
fi

# Send notification about retry - use specific job URL if found
# Use printf to create actual newlines (not literal \n)
if [ "$TEST_MODE" = "true" ]; then
    RETRY_MSG=$(printf ':test_tube: *[TEST MODE]* Re-running job for testing:\n<%s|View retry job>\n\n_Workflow:_ %s\n_Job:_ %s' "$EARLY_RETRY_JOB_URL" "$WORKFLOW_NAME" "$JOB_NAME")
else
    RETRY_MSG=$(printf ':arrows_counterclockwise: *Deterministic failure suspected.* Re-running job to confirm:\n<%s|View retry job>\n\n_Workflow:_ %s\n_Job:_ %s' "$EARLY_RETRY_JOB_URL" "$WORKFLOW_NAME" "$JOB_NAME")
fi
send_retry_notification "$RETRY_MSG"

# Wait for job to complete
echo -e "${BLUE}Waiting for retry job to complete...${NC}"
MAX_WAIT_MINUTES=180  # 3 hours max wait
WAIT_INTERVAL=60  # Check every minute
WAITED=0

# Track whether we're polling the specific job or the whole run
POLLING_JOB="false"
POLL_JOB_ID=""

# If we found the specific job ID early, we can poll it directly
if [ -n "$EARLY_JOB_ID" ] && [ "$EARLY_JOB_ID" != "null" ]; then
    POLLING_JOB="true"
    POLL_JOB_ID="$EARLY_JOB_ID"
    echo -e "${BLUE}Will poll specific job: ${POLL_JOB_ID}${NC}"
else
    echo -e "${BLUE}Will poll entire run (specific job ID not found yet)${NC}"
fi

while [ $WAITED -lt $((MAX_WAIT_MINUTES * 60)) ]; do
    sleep $WAIT_INTERVAL
    WAITED=$((WAITED + WAIT_INTERVAL))
    
    echo -e "${BLUE}Checking status (waited ${WAITED}s)...${NC}"
    
    if [ "$POLLING_JOB" = "true" ] && [ -n "$POLL_JOB_ID" ]; then
        # Poll the specific job status
        JOB_STATUS_RESP=$(gh api "repos/${OWNER}/${REPO}/actions/jobs/${POLL_JOB_ID}" 2>/dev/null || echo "{}")
        STATUS=$(echo "$JOB_STATUS_RESP" | jq -r '.status // "unknown"')
        CONCLUSION=$(echo "$JOB_STATUS_RESP" | jq -r '.conclusion // "null"')
        echo -e "  Job ${POLL_JOB_ID} - Status: ${STATUS}, Conclusion: ${CONCLUSION}"
    else
        # Poll the run status (fallback)
        RUN_STATUS=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}" 2>/dev/null || echo "{}")
        STATUS=$(echo "$RUN_STATUS" | jq -r '.status // "unknown"')
        CONCLUSION=$(echo "$RUN_STATUS" | jq -r '.conclusion // "null"')
        echo -e "  Run - Status: ${STATUS}, Conclusion: ${CONCLUSION}"
        
        # Try to find the specific job ID if we don't have it yet
        if [ "$POLLING_JOB" = "false" ]; then
            ATTEMPT_JOBS=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}/attempts/${NEW_ATTEMPT}/jobs?per_page=100" 2>/dev/null || echo '{"jobs":[]}')
            JOB_NAME_LOWER=$(echo "$JOB_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[–—−‐‑‒]/-/g')
            FOUND_JOB_ID=$(echo "$ATTEMPT_JOBS" | jq -r --arg name "$JOB_NAME_LOWER" '
                def normalize: ascii_downcase | gsub("[–—−‐‑‒]"; "-");
                .jobs // [] | 
                map(select((.name | normalize) == $name or (.name | normalize | contains($name)) or ($name | contains(.name | normalize)))) | 
                first | .id // empty
            ' 2>/dev/null || echo "")
            
            if [ -n "$FOUND_JOB_ID" ] && [ "$FOUND_JOB_ID" != "null" ]; then
                echo -e "${GREEN}  Found specific job ID: ${FOUND_JOB_ID}, switching to job polling${NC}"
                POLLING_JOB="true"
                POLL_JOB_ID="$FOUND_JOB_ID"
            fi
        fi
    fi
    
    # Check if job is done - either status is completed, or conclusion is set (cancelled can appear while status is still in_progress)
    if [ "$STATUS" = "completed" ] || [ "$CONCLUSION" = "cancelled" ] || [ "$CONCLUSION" = "failure" ] || [ "$CONCLUSION" = "success" ]; then
        if [ "$CONCLUSION" = "cancelled" ]; then
            echo -e "${YELLOW}Retry job was cancelled${NC}"
        else
            echo -e "${GREEN}Retry job completed with conclusion: ${CONCLUSION}${NC}"
        fi
        break
    fi
    
    if [ "$STATUS" = "unknown" ]; then
        echo -e "${RED}Failed to get status${NC}"
        send_retry_notification "$(printf ':warning: *Retry status check failed.*\n\nCould not get job/run status. Proceeding with original analysis.')"
        exit 0
    fi
done

if [ "$STATUS" != "completed" ]; then
    echo -e "${RED}Timeout waiting for retry job to complete (${MAX_WAIT_MINUTES} minutes)${NC}"
    send_retry_notification "$(printf ':hourglass: *Retry timed out.*\n\nJob did not complete within %d minutes.\nProceeding with original analysis.\n\n_Check the retry run:_ <%s|link>' "$MAX_WAIT_MINUTES" "$NEW_RUN_URL")"
    
    # Add note to slack message
    if [ -f "$SLACK_MSG_PATH" ]; then
        EXISTING_NOTES=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
        RETRY_NOTE="*NOTE:* An automatic retry was triggered but timed out before completing. The analysis below is based on the original failure only."
        if [ -n "$EXISTING_NOTES" ] && [ "$EXISTING_NOTES" != "null" ]; then
            COMBINED_NOTES="${EXISTING_NOTES}

---

${RETRY_NOTE}"
        else
            COMBINED_NOTES="${RETRY_NOTE}"
        fi
        jq --arg notes "$COMBINED_NOTES" '.notes = $notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
        mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    fi
    
    exit 0
fi

# ============================================================================
# Find the specific retry job results
# IMPORTANT: Use the job ID we've been tracking to avoid comparing wrong jobs
# ============================================================================
echo -e "${BLUE}Finding retry job results...${NC}"

RETRY_JOB=""
RETRY_JOB_ID=""
RETRY_JOB_CONCLUSION=""

# FIRST: If we have a tracked job ID from polling, use it directly
# This is the SAFEST approach - we know this is the exact job we retried
if [ -n "$POLL_JOB_ID" ] && [ "$POLL_JOB_ID" != "null" ]; then
    echo -e "${GREEN}Using tracked job ID from polling: ${POLL_JOB_ID}${NC}"
    RETRY_JOB=$(gh api "repos/${OWNER}/${REPO}/actions/jobs/${POLL_JOB_ID}" 2>/dev/null || echo "{}")
    RETRY_JOB_ID="$POLL_JOB_ID"
    RETRY_JOB_CONCLUSION=$(echo "$RETRY_JOB" | jq -r '.conclusion // "unknown"')
    RETRY_JOB_NAME=$(echo "$RETRY_JOB" | jq -r '.name // "unknown"')
    echo -e "${GREEN}Confirmed job: ${RETRY_JOB_NAME}${NC}"
    echo -e "${GREEN}Conclusion: ${RETRY_JOB_CONCLUSION}${NC}"
fi

# SECOND: If we don't have a tracked job ID, search by name (but be strict)
if [ -z "$RETRY_JOB_ID" ] || [ "$RETRY_JOB_ID" = "null" ]; then
    echo -e "${YELLOW}No tracked job ID, searching by name...${NC}"
    
    RETRY_JOBS=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}/attempts/${NEW_ATTEMPT}/jobs?per_page=100" 2>&1 || echo "{}")
    
    # Debug: Check what we got back
    RESPONSE_TYPE=$(echo "$RETRY_JOBS" | jq -r 'type' 2>/dev/null || echo "invalid")
    
    if [ "$RESPONSE_TYPE" = "invalid" ] || [ "$RESPONSE_TYPE" = "string" ]; then
        echo -e "${RED}API returned invalid response or error: ${RETRY_JOBS}${NC}"
        echo -e "${YELLOW}Proceeding with original analysis${NC}"
        send_retry_notification "$(printf ':warning: *Could not get retry results.*\n\nAPI returned invalid response. Proceeding with original analysis.')"
        exit 0
    fi
    
    JOBS_COUNT=$(echo "$RETRY_JOBS" | jq '.jobs | length' 2>/dev/null || echo "0")
    echo -e "${BLUE}Found ${JOBS_COUNT} jobs in retry attempt${NC}"
    
    if [ "$JOBS_COUNT" = "0" ]; then
        echo -e "${YELLOW}No jobs found in retry attempt, checking main jobs endpoint...${NC}"
        RETRY_JOBS=$(gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}/jobs?per_page=100" 2>&1 || echo "{}")
        JOBS_COUNT=$(echo "$RETRY_JOBS" | jq '.jobs | length' 2>/dev/null || echo "0")
        echo -e "${BLUE}Found ${JOBS_COUNT} jobs from main endpoint${NC}"
    fi
    
    # Normalize the job name for matching (handle unicode dashes, lowercase)
    normalize_name() {
        echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[–—−‐‑‒]/-/g'
    }
    
    JOB_NAME_NORMALIZED=$(normalize_name "$JOB_NAME")
    echo -e "${BLUE}Looking for job matching: ${JOB_NAME_NORMALIZED}${NC}"
    
    # List all job names for debugging
    echo -e "${BLUE}All jobs in response:${NC}"
    echo "$RETRY_JOBS" | jq -r '.jobs // [] | .[].name' 2>/dev/null || echo "(none)"
    
    # Find the matching job by name - try exact match first, then partial
    RETRY_JOB=$(echo "$RETRY_JOBS" | jq --arg name "$JOB_NAME_NORMALIZED" '
        def normalize: ascii_downcase | gsub("[–—−‐‑‒]"; "-");
        .jobs // [] | 
        map(select((.name | normalize) == $name or (.name | normalize | contains($name)) or ($name | contains(.name | normalize)))) |
        sort_by(.status == "completed" | not) |
        first // null
    ' 2>/dev/null || echo "null")
    
    # REMOVED: Dangerous fallback to "any failed job" - this caused comparing wrong jobs!
    # If we can't find the specific job by name, we should NOT proceed with comparison
    
    if [ "$RETRY_JOB" = "null" ] || [ -z "$RETRY_JOB" ]; then
        echo -e "${RED}ERROR: Could not find retry job by name match${NC}"
        echo -e "${RED}Job name we're looking for: ${JOB_NAME}${NC}"
        echo -e "${RED}This is a safety check to prevent comparing wrong jobs${NC}"
        echo -e "${YELLOW}Proceeding with original analysis (no retry comparison)${NC}"
        
        # Send notification that we couldn't determine retry result
        send_retry_notification "$(printf ':warning: *Retry was triggered but could not verify results.*\n\nCould not find the specific job in retry attempt.\nProceeding with original analysis.\n\n_Job name:_ %s' "$JOB_NAME")"
        
        # Add note to slack message
        if [ -f "$SLACK_MSG_PATH" ]; then
            EXISTING_NOTES=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
            RETRY_NOTE="*NOTE:* An automatic retry was triggered but we could not verify the results (job not found in retry attempt). The analysis below is based on the original failure only."
            if [ -n "$EXISTING_NOTES" ] && [ "$EXISTING_NOTES" != "null" ]; then
                COMBINED_NOTES="${EXISTING_NOTES}

---

${RETRY_NOTE}"
            else
                COMBINED_NOTES="${RETRY_NOTE}"
            fi
            jq --arg notes "$COMBINED_NOTES" '.notes = $notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
            mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
        fi
        
        exit 0
    fi
    
    RETRY_JOB_ID=$(echo "$RETRY_JOB" | jq -r '.id // ""')
    RETRY_JOB_CONCLUSION=$(echo "$RETRY_JOB" | jq -r '.conclusion // "unknown"')
    RETRY_JOB_NAME=$(echo "$RETRY_JOB" | jq -r '.name // "unknown"')
    echo -e "${GREEN}Found job by name: ${RETRY_JOB_NAME} (ID: ${RETRY_JOB_ID})${NC}"
fi

if [ -z "$RETRY_JOB_ID" ] || [ "$RETRY_JOB_ID" = "null" ]; then
    echo -e "${RED}Could not find retry job ID${NC}"
    echo -e "${YELLOW}Proceeding with original analysis${NC}"
    send_retry_notification "$(printf ':warning: *Could not verify retry results.*\n\nJob ID not found. Proceeding with original analysis.')"
    
    # Add note to slack message
    if [ -f "$SLACK_MSG_PATH" ]; then
        EXISTING_NOTES=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
        RETRY_NOTE="*NOTE:* An automatic retry was triggered but we could not verify the results. The analysis below is based on the original failure only."
        if [ -n "$EXISTING_NOTES" ] && [ "$EXISTING_NOTES" != "null" ]; then
            COMBINED_NOTES="${EXISTING_NOTES}

---

${RETRY_NOTE}"
        else
            COMBINED_NOTES="${RETRY_NOTE}"
        fi
        jq --arg notes "$COMBINED_NOTES" '.notes = $notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
        mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    fi
    
    exit 0
fi

RETRY_JOB_URL="https://github.com/${OWNER}/${REPO}/actions/runs/${RUN_ID}/job/${RETRY_JOB_ID}"

echo -e "${BLUE}Retry job ID: ${RETRY_JOB_ID}${NC}"
echo -e "${BLUE}Retry job conclusion: ${RETRY_JOB_CONCLUSION}${NC}"
echo -e "${BLUE}Retry job URL: ${RETRY_JOB_URL}${NC}"

# TEST_MODE: Skip all outcome handling, just send notification and proceed with original message
if [ "$TEST_MODE" = "true" ]; then
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}TEST MODE: Retry completed with conclusion: ${RETRY_JOB_CONCLUSION}${NC}"
    echo -e "${YELLOW}TEST MODE: Skipping message modifications, will send original message${NC}"
    echo -e "${YELLOW}========================================${NC}"
    
    # Send a test notification about the result
    if [ "$RETRY_JOB_CONCLUSION" = "success" ]; then
        send_retry_notification "$(printf ':test_tube: *[TEST MODE]* Retry completed: *PASSED*\n\nRetry run: <%s|link>\n\n_Original message will be sent unchanged._' "$RETRY_JOB_URL")"
    elif [ "$RETRY_JOB_CONCLUSION" = "failure" ]; then
        send_retry_notification "$(printf ':test_tube: *[TEST MODE]* Retry completed: *FAILED*\n\nRetry run: <%s|link>\n\n_Original message will be sent unchanged._' "$RETRY_JOB_URL")"
    else
        send_retry_notification "$(printf ':test_tube: *[TEST MODE]* Retry completed: *%s*\n\nRetry run: <%s|link>\n\n_Original message will be sent unchanged._' "$RETRY_JOB_CONCLUSION" "$RETRY_JOB_URL")"
    fi
    
    echo -e "${GREEN}TEST MODE: Retry logic completed, proceeding with original analysis${NC}"
    exit 0
fi

# Handle the three outcomes (normal mode)
if [ "$RETRY_JOB_CONCLUSION" = "success" ]; then
    # ========================================
    # CASE: Retry PASSED - Convert to Case 3
    # ========================================
    echo -e "${GREEN}Retry passed! Converting to Case 3 (non-deterministic)${NC}"
    
    jq -n --arg result "passed" --arg msg "Retry passed, failure is non-deterministic" \
        '{result: $result, message: $msg}' > "$RETRY_RESULT_FILE"
    
    # Update slack_message.json to Case 3
    # IMPORTANT: Preserve existing notes and append retry info
    FAILURE_MSG=$(jq -r '.failure_message // "Unknown error"' "$SLACK_MSG_PATH")
    EXISTING_NOTES=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
    
    # Build the retry note
    RETRY_NOTE="*RETRY PASSED - CONVERTED TO CASE 3:* This failure passed on automatic retry, indicating a non-deterministic/flaky issue rather than a code regression.
- Original failure: ${FAILING_RUN_URL}
- Successful retry: ${RETRY_JOB_URL}"
    
    # Combine existing notes with retry note
    if [ -n "$EXISTING_NOTES" ] && [ "$EXISTING_NOTES" != "null" ]; then
        COMBINED_NOTES="${EXISTING_NOTES}

---

${RETRY_NOTE}"
    else
        COMBINED_NOTES="${RETRY_NOTE}"
    fi
    
    jq --arg scenario "Failure likely outside tt-metal" \
       --arg case_num "3" \
       --arg combined_notes "$COMBINED_NOTES" \
       --arg slack_msg "Failure is non-deterministic. The job passed on retry. Please investigate flakiness or infrastructure issues." \
       '. + {
           scenario: $scenario,
           case: $case_num,
           notes: $combined_notes,
           slack_message: $slack_msg,
           commits: []
       }' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
    mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    
    echo -e "${GREEN}Updated slack_message.json - preserved original notes and added retry info${NC}"
    
    # Update explanation.md
    cat > "$EXPLANATION_PATH" << EOF
# Auto Triage Explanation: ${JOB_NAME}

## Failure is Non-Deterministic (Passed on Retry)

The original analysis suspected a deterministic failure, but an automatic retry of the job **passed successfully**.

This indicates the failure is likely:
- A flaky test
- An infrastructure/timing issue
- A transient environmental problem

### Original Failure
- **Link:** [${FAILING_RUN_URL}](${FAILING_RUN_URL})
- **Error Message:**
\`\`\`
${ORIGINAL_ERROR}
\`\`\`

### Successful Retry
- **Link:** [${RETRY_JOB_URL}](${RETRY_JOB_URL})

### Recommendation
Investigate test flakiness or infrastructure stability. No code changes appear to be required.

---
_This analysis was performed automatically by the auto-triage system._
EOF

    send_retry_notification "$(printf ':white_check_mark: *Retry passed!* Failure appears to be non-deterministic.\n\nOriginal failure: <%s|link>\nSuccessful retry: <%s|link>' "$FAILING_RUN_URL" "$RETRY_JOB_URL")"

elif [ "$RETRY_JOB_CONCLUSION" = "failure" ]; then
    # ========================================
    # CASE: Retry FAILED - Need to compare errors
    # ========================================
    echo -e "${YELLOW}Retry also failed. Comparing error messages...${NC}"
    
    # Download retry job logs/annotations
    RETRY_LOGS_DIR="${LOGS_DIR}/retry_job_${RETRY_JOB_ID}"
    mkdir -p "$RETRY_LOGS_DIR"
    
    # Always fetch both annotations AND logs - logs often have better error details
    echo -e "${BLUE}Fetching retry job annotations...${NC}"
    "${ROOT}/get_annotations.sh" "$RETRY_JOB_URL" "${RETRY_LOGS_DIR}/annotations.json" 2>/dev/null || true
    
    echo -e "${BLUE}Fetching retry job logs...${NC}"
    "${ROOT}/get_logs.sh" "$RETRY_JOB_URL" "${LOGS_DIR}/retry" 2>/dev/null || true
    
    # Extract error from logs FIRST (usually has better details than annotations)
    RETRY_ERROR=""
    RETRY_ERROR_FROM_LOGS=""
    RETRY_ERROR_FROM_ANNOTATIONS=""
    
    # Try to get error from logs
    LOG_DIR_PATH="${LOGS_DIR}/retry/job_${RETRY_JOB_ID}"
    if [ -d "$LOG_DIR_PATH" ]; then
        echo -e "${BLUE}Searching logs in: ${LOG_DIR_PATH}${NC}"
        # Look for common error patterns in log files
        RETRY_ERROR_FROM_LOGS=$(find "$LOG_DIR_PATH" -name "*.txt" -exec grep -h -B2 -A10 -E "(FAILED|ERROR:|Exception:|AssertionError|pytest.*failed|collection.*error|ModuleNotFoundError|ImportError|TypeError:|ValueError:|RuntimeError:)" {} \; 2>/dev/null | head -50 || echo "")
        
        # If that didn't work, try a broader search
        if [ -z "$RETRY_ERROR_FROM_LOGS" ]; then
            RETRY_ERROR_FROM_LOGS=$(find "$LOG_DIR_PATH" -name "*.txt" -exec grep -h -E "(fail|error|exception)" {} \; 2>/dev/null | head -30 || echo "")
        fi
    fi
    
    # Also try the full logs directory
    if [ -z "$RETRY_ERROR_FROM_LOGS" ] && [ -d "${LOGS_DIR}/retry/full" ]; then
        echo -e "${BLUE}Searching full logs directory...${NC}"
        RETRY_ERROR_FROM_LOGS=$(find "${LOGS_DIR}/retry/full" -name "*.txt" -exec grep -h -B2 -A10 -E "(FAILED|ERROR:|Exception:|AssertionError|pytest.*failed|collection.*error)" {} \; 2>/dev/null | head -50 || echo "")
    fi
    
    # Get error from annotations as fallback
    if [ -f "${RETRY_LOGS_DIR}/annotations.json" ]; then
        RETRY_ERROR_FROM_ANNOTATIONS=$(jq -r '
            [.[] | select((.annotation_level | ascii_downcase) == "failure" or (.annotation_level | ascii_downcase) == "error")] |
            map(.message // .raw_details // "") |
            map(select(. != "")) |
            join("\n") // ""
        ' "${RETRY_LOGS_DIR}/annotations.json" 2>/dev/null || echo "")
    fi
    
    # Prefer logs over annotations (logs usually have more detail)
    # But if logs only have generic output, use annotations
    if [ -n "$RETRY_ERROR_FROM_LOGS" ] && [ ${#RETRY_ERROR_FROM_LOGS} -gt 50 ]; then
        echo -e "${GREEN}Using error from logs (${#RETRY_ERROR_FROM_LOGS} chars)${NC}"
        RETRY_ERROR="$RETRY_ERROR_FROM_LOGS"
    elif [ -n "$RETRY_ERROR_FROM_ANNOTATIONS" ]; then
        echo -e "${YELLOW}Using error from annotations (${#RETRY_ERROR_FROM_ANNOTATIONS} chars)${NC}"
        RETRY_ERROR="$RETRY_ERROR_FROM_ANNOTATIONS"
    elif [ -n "$RETRY_ERROR_FROM_LOGS" ]; then
        echo -e "${YELLOW}Using short error from logs (${#RETRY_ERROR_FROM_LOGS} chars)${NC}"
        RETRY_ERROR="$RETRY_ERROR_FROM_LOGS"
    fi
    
    if [ -z "$RETRY_ERROR" ]; then
        echo -e "${RED}Could not extract error from retry job${NC}"
        RETRY_ERROR="Could not extract error message from retry job"
    fi
    
    echo -e "${BLUE}Retry error preview (first 200 chars):${NC}"
    echo "${RETRY_ERROR:0:200}"
    
    # Save retry error for comparison
    echo "$RETRY_ERROR" > "${DATA_DIR}/retry_error.txt"
    
    # Call Copilot to compare errors
    echo -e "${BLUE}Calling Copilot to compare error messages...${NC}"
    
    COMPARE_INSTRUCTIONS="${ROOT}/compare_errors_instructions.txt"
    if [ ! -f "$COMPARE_INSTRUCTIONS" ]; then
        echo -e "${RED}compare_errors_instructions.txt not found${NC}"
        # Default to assuming different errors if we can't compare
        SAME_FAILURE="false"
    else
        read -r -d '' COMPARE_PROMPT <<EOF || true
You are operating in a CI environment. Compare two error messages and determine if they represent the same failure.

$(cat "$COMPARE_INSTRUCTIONS")
EOF
        
        # Ensure COPILOT_GITHUB_TOKEN is set
        if [ -z "${COPILOT_GITHUB_TOKEN:-}" ]; then
            export COPILOT_GITHUB_TOKEN="${GH_TOKEN:-}"
        fi
        
        # Run Copilot comparison
        cd "$ROOT"
        copilot -p "$COMPARE_PROMPT" --allow-all-tools 2>/dev/null || true
        
        # Read the comparison result
        COMPARISON_FILE="${DATA_DIR}/error_comparison.json"
        if [ -f "$COMPARISON_FILE" ]; then
            SAME_FAILURE=$(jq -r '.same_failure // false' "$COMPARISON_FILE")
        else
            echo -e "${YELLOW}No comparison result, assuming different failures${NC}"
            SAME_FAILURE="false"
        fi
    fi
    
    echo -e "${BLUE}Same failure: ${SAME_FAILURE}${NC}"
    
    if [ "$SAME_FAILURE" = "true" ]; then
        # ========================================
        # SUB-CASE: Failed with SAME error
        # ========================================
        echo -e "${RED}Retry failed with SAME error - confirming deterministic failure${NC}"
        
        jq -n --arg result "failed_same" --arg msg "Retry failed with same error, confirming deterministic issue" \
            '{result: $result, message: $msg}' > "$RETRY_RESULT_FILE"
        
        # Add note to original slack message about confirmed failure
        # IMPORTANT: Preserve existing notes and append retry confirmation
        echo -e "${BLUE}Adding retry confirmation note to slack_message.json...${NC}"
        echo -e "${BLUE}  Source file: ${SLACK_MSG_PATH}${NC}"
        
        if [ ! -f "$SLACK_MSG_PATH" ]; then
            echo -e "${RED}ERROR: slack_message.json not found at ${SLACK_MSG_PATH}${NC}"
        else
            echo -e "${GREEN}  File exists, size: $(wc -c < "$SLACK_MSG_PATH") bytes${NC}"
            
            # Get existing notes first
            EXISTING_NOTES=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
            echo -e "${BLUE}  Existing notes length: ${#EXISTING_NOTES} chars${NC}"
            
            # Build the retry note
            RETRY_NOTE="*RETRY CONFIRMED DETERMINISTIC ISSUE:* The job was automatically retried and failed with the same error.
- Retry link: ${RETRY_JOB_URL}
- _Failing retry indicates this is a genuine deterministic issue, not flakiness._"
            
            # Combine existing notes with retry note
            if [ -n "$EXISTING_NOTES" ] && [ "$EXISTING_NOTES" != "null" ]; then
                COMBINED_NOTES="${EXISTING_NOTES}

---

${RETRY_NOTE}"
            else
                COMBINED_NOTES="${RETRY_NOTE}"
            fi
            
            jq --arg combined_notes "$COMBINED_NOTES" '.notes = $combined_notes' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
            
            if [ -f "${SLACK_MSG_PATH}.tmp" ]; then
                mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
                echo -e "${GREEN}  Updated slack_message.json successfully${NC}"
                echo -e "${GREEN}  New notes length: ${#COMBINED_NOTES} chars${NC}"
            else
                echo -e "${RED}ERROR: Failed to create temp file${NC}"
            fi
        fi
        
        # Prepend note to explanation.md
        echo -e "${BLUE}Prepending retry confirmation note to explanation.md...${NC}"
        echo -e "${BLUE}  Source file: ${EXPLANATION_PATH}${NC}"
        EXISTING_EXPLANATION=$(cat "$EXPLANATION_PATH" 2>/dev/null || echo "")
        cat > "$EXPLANATION_PATH" << EOF
## Failure Was Repeatable (Confirmed Deterministic)

The job was automatically retried and **failed with the same error**, confirming this is a deterministic issue.

- **First failure:** [${FAILING_RUN_URL}](${FAILING_RUN_URL})
- **Retry failure:** [${RETRY_JOB_URL}](${RETRY_JOB_URL})

---

${EXISTING_EXPLANATION}
EOF

        echo -e "${GREEN}Updated explanation.md with retry confirmation${NC}"
        echo -e "${BLUE}Sending Slack notification about confirmed deterministic failure...${NC}"
        send_retry_notification "$(printf ':x: *Retry also failed with the same error.* Deterministic failure confirmed.\n\nFirst failure: <%s|link>\nRetry failure: <%s|link>' "$FAILING_RUN_URL" "$RETRY_JOB_URL")"
        
    else
        # ========================================
        # SUB-CASE: Failed with DIFFERENT error
        # ========================================
        echo -e "${YELLOW}Retry failed with DIFFERENT error - both failures appear non-deterministic${NC}"
        
        # Get the LLM-extracted retry error from the comparison JSON (much better than our grep extraction)
        RETRY_ERROR_FROM_LLM=""
        if [ -f "$COMPARISON_FILE" ]; then
            RETRY_ERROR_FROM_LLM=$(jq -r '.retry_error_extracted // ""' "$COMPARISON_FILE")
        fi
        
        # Use LLM-extracted error if available, otherwise fall back to our extraction
        if [ -n "$RETRY_ERROR_FROM_LLM" ] && [ "$RETRY_ERROR_FROM_LLM" != "null" ]; then
            echo -e "${GREEN}Using LLM-extracted retry error${NC}"
            RETRY_ERR_FOR_NOTES="$RETRY_ERROR_FROM_LLM"
        else
            echo -e "${YELLOW}LLM didn't extract retry error, using our extraction${NC}"
            RETRY_ERR_FOR_NOTES=$(echo "$RETRY_ERROR" | head -c 500)
        fi
        
        jq -n --arg result "failed_different" --arg msg "Retry failed with different error, both appear non-deterministic" \
            --arg retry_url "$RETRY_JOB_URL" --arg retry_error "$RETRY_ERR_FOR_NOTES" \
            '{result: $result, message: $msg, retry_url: $retry_url, retry_error: $retry_error}' > "$RETRY_RESULT_FILE"
        
        # Update original slack message to Case 3
        # IMPORTANT: Preserve existing notes and append retry info
        # NOTE: Original error is already in failure_message field, so only show retry error
        EXISTING_NOTES=$(jq -r '.notes // ""' "$SLACK_MSG_PATH")
        
        # Build the retry note - only show retry error since original is already in failure_message
        RETRY_NOTE="*RETRY FAILED WITH DIFFERENT ERROR - CONVERTED TO CASE 3:*
The retry failed with a *different* error than the original failure, suggesting non-deterministic issues rather than a code regression.

*Retry error:*
\`\`\`
${RETRY_ERR_FOR_NOTES}
\`\`\`
Retry link: ${RETRY_JOB_URL}"
        
        # Combine existing notes with retry note
        if [ -n "$EXISTING_NOTES" ] && [ "$EXISTING_NOTES" != "null" ]; then
            COMBINED_NOTES="${EXISTING_NOTES}

---

${RETRY_NOTE}"
        else
            COMBINED_NOTES="${RETRY_NOTE}"
        fi
        
        # Use jq's proper string escaping for the error messages
        jq --arg scenario "Failure likely outside tt-metal" \
           --arg case_num "3" \
           --arg combined_notes "$COMBINED_NOTES" \
           --arg slack_msg "Failure appears non-deterministic. Two consecutive runs failed with different errors. Please investigate test flakiness or infrastructure issues." \
           '. + {
               scenario: $scenario,
               case: $case_num,
               notes: $combined_notes,
               slack_message: $slack_msg,
               commits: []
           }' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
        mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
        
        echo -e "${GREEN}Updated slack_message.json - preserved original notes and added retry info${NC}"
        
        # Update explanation.md
        cat > "$EXPLANATION_PATH" << EOF
# Auto Triage Explanation: ${JOB_NAME}

## Failure Seems Non-Deterministic (Different Errors on Retry)

The original analysis suspected a deterministic failure, but an automatic retry failed with a **different error message**.

This suggests both failures may be:
- Flaky tests with multiple failure modes
- Infrastructure instability
- Race conditions or timing issues

### First Failure
- **Link:** [${FAILING_RUN_URL}](${FAILING_RUN_URL})
- **Error Message:**
\`\`\`
${ORIGINAL_ERROR}
\`\`\`

### Second Failure (Retry)
- **Link:** [${RETRY_JOB_URL}](${RETRY_JOB_URL})
- **Error Message:**
\`\`\`
${RETRY_ERROR}
\`\`\`

### Recommendation
Both failures should be investigated as potential flakiness issues. The different error messages suggest this is not a simple code regression.

---
_This analysis was performed automatically by the auto-triage system._
EOF

        send_retry_notification "$(printf ':warning: *Retry failed with a DIFFERENT error.* Both failures appear non-deterministic.\n\nFirst failure: <%s|link>\nRetry failure: <%s|link>\n\n_Different error messages suggest flakiness rather than a code regression._' "$FAILING_RUN_URL" "$RETRY_JOB_URL")"
    fi
elif [ "$RETRY_JOB_CONCLUSION" = "cancelled" ]; then
    # ========================================
    # CASE: Retry was CANCELLED - Send original message with note
    # ========================================
    echo -e "${YELLOW}Retry job was cancelled${NC}"
    echo -e "${YELLOW}Sending original message with cancellation note${NC}"
    
    jq -n --arg result "cancelled" --arg msg "Retry was cancelled, sending original analysis" \
        '{result: $result, message: $msg}' > "$RETRY_RESULT_FILE"
    
    # Add note to original slack message about cancelled retry
    jq --arg retry_url "$RETRY_JOB_URL" '
        .notes = ((.notes // "") + "\n\n*NOTE:* An automatic retry was attempted but was cancelled before completion.\n- Retry link: " + $retry_url + "\n- _The analysis above is based on the original failure only._")
    ' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
    mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    
    # Prepend note to explanation.md
    EXISTING_EXPLANATION=$(cat "$EXPLANATION_PATH" 2>/dev/null || echo "")
    cat > "$EXPLANATION_PATH" << EOF
## Note: Retry Was Cancelled

An automatic retry was triggered to confirm whether this failure is deterministic, but the retry job was **cancelled** before completion.

- **Retry link:** [${RETRY_JOB_URL}](${RETRY_JOB_URL})

The analysis below is based on the original failure only.

---

${EXISTING_EXPLANATION}
EOF

    send_retry_notification "$(printf ':no_entry_sign: *Retry was cancelled.* Sending original analysis.\n\nOriginal failure: <%s|link>\nCancelled retry: <%s|link>' "$FAILING_RUN_URL" "$RETRY_JOB_URL")"

else
    # Unknown conclusion (skipped, etc.)
    echo -e "${YELLOW}Retry job had unexpected conclusion: ${RETRY_JOB_CONCLUSION}${NC}"
    echo -e "${YELLOW}Sending original message with note about unexpected retry status${NC}"
    
    jq -n --arg result "unknown" --arg msg "Retry had unexpected conclusion: $RETRY_JOB_CONCLUSION" \
        --arg conclusion "$RETRY_JOB_CONCLUSION" \
        '{result: $result, message: $msg, conclusion: $conclusion}' > "$RETRY_RESULT_FILE"
    
    # Add note to original slack message
    jq --arg retry_url "$RETRY_JOB_URL" --arg conclusion "$RETRY_JOB_CONCLUSION" '
        .notes = ((.notes // "") + "\n\n*NOTE:* An automatic retry was attempted but ended with status: " + $conclusion + "\n- Retry link: " + $retry_url + "\n- _The analysis above is based on the original failure only._")
    ' "$SLACK_MSG_PATH" > "${SLACK_MSG_PATH}.tmp"
    mv "${SLACK_MSG_PATH}.tmp" "$SLACK_MSG_PATH"
    
    send_retry_notification "$(printf ':grey_question: *Retry ended with unexpected status: %s*\n\nOriginal failure: <%s|link>\nRetry: <%s|link>\n\n_Sending original analysis._' "$RETRY_JOB_CONCLUSION" "$FAILING_RUN_URL" "$RETRY_JOB_URL")"
fi

echo -e "${GREEN}Retry logic completed${NC}"

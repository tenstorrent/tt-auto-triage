#!/bin/bash

# Script to find the last successful and first failing run of a specific subjob
# Usage: ./find_boundaries.sh <workflow_name> <subjob_name>
# Example: ./find_boundaries.sh single-card-demo-tests yolov5x-N150-func

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# TESTING: COMMIT CUTOFF FILTER
# Set this to a commit SHA to ignore all runs on commits NEWER than this one.
# This is useful for testing retry logic on older failures when newer runs
# have already passed.
# Leave empty ("") for normal behavior (no filtering).
# Can be set via environment variable CUTOFF_COMMIT or passed as input to the action.
# Example: CUTOFF_COMMIT="abc123def456"
# ============================================================================
# Default to empty if not set via environment variable
CUTOFF_COMMIT="${CUTOFF_COMMIT:-}"
# ============================================================================

# Function to check if commit A is newer than commit B (A is descendant of B)
# Returns 0 (true) if A is strictly newer than B, 1 (false) otherwise
is_commit_newer() {
    local commit_a="$1"
    local commit_b="$2"
    
    # If either commit is empty, can't compare
    if [ -z "$commit_a" ] || [ -z "$commit_b" ]; then
        return 1
    fi
    
    # If commits are the same, A is not "newer"
    if [ "$commit_a" = "$commit_b" ]; then
        return 1
    fi
    
    # Check if commit_a is a descendant of commit_b (i.e., A came after B)
    # git merge-base --is-ancestor returns 0 if $commit_b is an ancestor of $commit_a
    if git merge-base --is-ancestor "$commit_b" "$commit_a" 2>/dev/null; then
        return 0  # A is newer than B
    else
        return 1  # A is not newer than B (or error)
    fi
}

# Check arguments
if [ $# -lt 2 ]; then
    echo -e "${RED}Error: Missing required arguments${NC}"
    echo "Usage: $0 <workflow_name> <subjob_name>"
    echo ""
    echo "Examples:"
    echo "  $0 single-card-demo-tests yolov5x-N150-func"
    echo "  $0 single-card-demo-tests vanilla_unet-N150-func"
    echo ""
    echo "The workflow_name should match the workflow file name (without .yaml extension)"
    exit 1
fi

WORKFLOW_NAME="$1"
SUBJOB_NAME="$2"

# Normalize Unicode dash/hyphen characters to ASCII '-'
normalize_hyphens() {
    python3 - "$1" <<'PY'
import sys, unicodedata
text = sys.argv[1]
print(''.join('-' if unicodedata.category(ch) == 'Pd' else ch for ch in text), end='')
PY
}

WORKFLOW_NAME=$(normalize_hyphens "$WORKFLOW_NAME")
SUBJOB_NAME=$(normalize_hyphens "$SUBJOB_NAME")

FAILURE_LIMIT=30
RUN_LIMIT_WITHOUT_SUCCESS=100
FAILURE_ONLY_COUNT=0
EXCEEDED_FAILURE_LIMIT=false
BOUNDARY_STATUS="ok"
BOUNDARY_MESSAGE=""

REPO="tenstorrent/tt-metal"
BASE_URL="https://github.com/${REPO}"
DATA_DIR="auto_triage/data"
SUMMARY_JSON_PATH="${DATA_DIR}/boundaries_summary.json"
RUNS_JSON_PATH="${DATA_DIR}/subjob_runs.json"
# cancel.json lives in the .auto_triage working directory, same as this script's CWD
CANCEL_FILE="cancel.json"

write_cancel_and_exit() {
    local message="$1"
    # Create cancel.json so the composite action can surface a Slack cancellation message
    tmp_cancel="$(mktemp)"
    jq -n --arg msg "$message" '{should_cancel: true, message: $msg}' > "$tmp_cancel"
    mv "$tmp_cancel" "$CANCEL_FILE"
    echo -e "${YELLOW}$message${NC}"
    echo -e "${YELLOW}Created ${CANCEL_FILE}; downstream stages will treat this as a cancellation.${NC}"
    exit 0
}

mkdir -p "$DATA_DIR"
rm -f "$SUMMARY_JSON_PATH" "$RUNS_JSON_PATH"

echo -e "${BLUE}Searching for workflow: ${GREEN}${WORKFLOW_NAME}${NC}"
echo -e "${BLUE}Looking for subjob: ${GREEN}${SUBJOB_NAME}${NC}"

# Show cutoff notice if set
if [ -n "$CUTOFF_COMMIT" ]; then
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}TESTING MODE: Cutoff commit filter active${NC}"
    echo -e "${YELLOW}Ignoring all runs on commits newer than: ${CUTOFF_COMMIT}${NC}"
    echo -e "${YELLOW}========================================${NC}"
fi
echo ""

# First, find the workflow ID (support both .yaml and .yml)
echo "Finding workflow ID..."
WORKFLOW_ID=""
for EXT in yaml yml YAML YML; do
    WORKFLOW_FILE="${WORKFLOW_NAME}.${EXT}"
    # Try to extract the workflow id; on HTTP errors gh may still return JSON without .id
    WORKFLOW_ID_RAW=$(gh api "repos/${REPO}/actions/workflows/${WORKFLOW_FILE}" 2>/dev/null || echo "")
    WORKFLOW_ID=$(printf '%s' "$WORKFLOW_ID_RAW" | jq -r '.id // empty' 2>/dev/null || echo "")
    if [ -n "$WORKFLOW_ID" ]; then
        WORKFLOW_NAME="${WORKFLOW_NAME}"
        WORKFLOW_FILENAME="$WORKFLOW_FILE"
        break
    fi
done

if [ -z "$WORKFLOW_ID" ]; then
    echo -e "${RED}Error: Could not find workflow '${WORKFLOW_NAME}' with .yaml or .yml extension${NC}"
    echo "Make sure the workflow file exists at: .github/workflows/${WORKFLOW_NAME}.yaml (or .yml)"
    # Instead of hard failing the whole job, signal a graceful cancellation so
    # the auto-triage action can send a Slack message explaining what happened.
    write_cancel_and_exit "Workflow '${WORKFLOW_NAME}' not found in repository ${REPO}. Verify file path."
fi

echo -e "${GREEN}Found workflow ID: ${WORKFLOW_ID}${NC}"
echo ""

echo -e "${GREEN}Found workflow ID: ${WORKFLOW_ID}${NC}"
echo ""

# Fetch workflow runs (limit to recent runs for performance, only main branch)
echo "Processing workflow runs page by page (this may take some time)..."
PER_PAGE=100
PAGE=1
TOTAL_RUNS_FETCHED=0
VALID_RUNS_FETCHED=0

LAST_SUCCESSFUL_RUN=""
LAST_SUCCESSFUL_RUN_ID=""
LAST_SUCCESSFUL_COMMIT=""
LAST_SUCCESSFUL_JOB_URL=""
FIRST_FAILING_RUN=""
FIRST_FAILING_RUN_ID=""
FIRST_FAILING_COMMIT=""
FIRST_FAILING_JOB_URL=""

PROCESSED=0
FOUND_SUCCESS=false
STOP_SEARCH=false
# Track whether we've ever seen the subjob at all
SUBJOB_EVER_FOUND=false
SUBJOB_MISSING_CANCEL_LIMIT=50

# Track the most recent failure we see, then when we find the last success,
# that failure is the first failure after the success
MOST_RECENT_FAILURE_RUN=""
MOST_RECENT_FAILURE_RUN_ID=""
MOST_RECENT_FAILURE_COMMIT=""
MOST_RECENT_FAILURE_JOB_URL=""
FAILED_RUNS_JSON='[]'
SUBJOB_RUNS_JSON='[]'

while true; do
    PAGE_RESPONSE=$(gh api "repos/${REPO}/actions/workflows/${WORKFLOW_ID}/runs?branch=main&per_page=${PER_PAGE}&page=${PAGE}" 2>/dev/null || echo "")
    if [ -z "$PAGE_RESPONSE" ]; then
        if [ "$PAGE" -eq 1 ]; then
            echo -e "${RED}Error: Could not fetch workflow runs${NC}"
            # Treat this as a cancellation so the caller can surface a clear message in Slack
            write_cancel_and_exit "Could not fetch workflow runs for workflow '${WORKFLOW_NAME}' (check that the workflow exists and that permissions are correct)."
        fi
        break
    fi

    RUNS_PAGE=$(echo "$PAGE_RESPONSE" | jq '.workflow_runs // []')
    PAGE_TOTAL=$(echo "$RUNS_PAGE" | jq 'length')

    if [ "$PAGE_TOTAL" -eq 0 ]; then
        if [ "$PAGE" -eq 1 ]; then
            echo -e "${RED}Error: No workflow runs returned${NC}"
            exit 1
        fi
        break
    fi

    TOTAL_RUNS_FETCHED=$((TOTAL_RUNS_FETCHED + PAGE_TOTAL))
    echo -e "${GREEN}Fetched ${TOTAL_RUNS_FETCHED} workflow runs so far (page ${PAGE})${NC}"

    VALID_PAGE=$(echo "$RUNS_PAGE" | jq -r "[.[] | select(.head_branch == \"main\" and ((.status == \"completed\") or (.status == \"in_progress\") or (.status == \"waiting\") or (.status == \"queued\")) and (.conclusion != \"cancelled\"))]")
    VALID_COUNT=$(echo "$VALID_PAGE" | jq 'length')

    if [ "$VALID_COUNT" -eq 0 ]; then
        PAGE=$((PAGE + 1))
        continue
    fi

    VALID_RUNS_FETCHED=$((VALID_RUNS_FETCHED + VALID_COUNT))
    echo -e "${GREEN}Valid runs accumulated: ${VALID_RUNS_FETCHED}${NC}"

    mapfile -t RUN_ROWS < <(echo "$VALID_PAGE" | jq -c '.[]')
    for RUN_DATA in "${RUN_ROWS[@]}"; do
        FOUND_JOB=false
        RUN_ID=$(echo "$RUN_DATA" | jq -r '.id')
        RUN_COMMIT=$(echo "$RUN_DATA" | jq -r '.head_sha')
        RUN_COMPLETED_AT=$(echo "$RUN_DATA" | jq -r '.updated_at // .run_started_at // "unknown"')
        RUN_URL="${BASE_URL}/actions/runs/${RUN_ID}"

        PROCESSED=$((PROCESSED + 1))
        
        # TESTING: Skip runs newer than cutoff commit if set
        if [ -n "$CUTOFF_COMMIT" ]; then
            if is_commit_newer "$RUN_COMMIT" "$CUTOFF_COMMIT"; then
                echo -e "[${PROCESSED}] Checking run ${RUN_ID} (${RUN_COMPLETED_AT})... ${YELLOW}SKIPPED (commit ${RUN_COMMIT:0:8} is newer than cutoff ${CUTOFF_COMMIT:0:8})${NC}"
                continue
            fi
        fi
        
        echo -n "[${PROCESSED}] Checking run ${RUN_ID} (${RUN_COMPLETED_AT})... "

        # Collect completed job attempts for this run across all workflow run attempts
        RUN_ATTEMPT=$(echo "$RUN_DATA" | jq -r '.run_attempt // 1')
        MATCHING_JOBS='[]'
        ATTEMPT="$RUN_ATTEMPT"
        while [ "$ATTEMPT" -ge 1 ]; do
            PAGE_J=1
            while true; do
                if [ "$ATTEMPT" -eq "$RUN_ATTEMPT" ]; then
                    ENDPOINT="repos/${REPO}/actions/runs/${RUN_ID}/jobs?per_page=${PER_PAGE}&page=${PAGE_J}"
                else
                    ENDPOINT="repos/${REPO}/actions/runs/${RUN_ID}/attempts/${ATTEMPT}/jobs?per_page=${PER_PAGE}&page=${PAGE_J}"
                fi
                PAGE_JOBS=$(gh api "$ENDPOINT" 2>/dev/null || echo "")

                if [ -z "$PAGE_JOBS" ]; then
                    break
                fi
                JOB_ENTRIES=$(echo "$PAGE_JOBS" | jq '.jobs // []')
                JOB_COUNT=$(echo "$JOB_ENTRIES" | jq 'length' 2>/dev/null || echo "0")
                if [ "$JOB_COUNT" -eq 0 ]; then
                    break
                fi

                FILTERED=$(echo "$JOB_ENTRIES" | jq \
                    --arg subjob "$SUBJOB_NAME" \
                    --arg workflow "$WORKFLOW_NAME" \
                    --argjson attempt "$ATTEMPT" -c '
                        def normalize_dash:
                          gsub("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]"; "-");
                        def match_subjob($name; $workflow; $subjob):
                          ($name | normalize_dash | ascii_downcase) as $n
                          | ($subjob | normalize_dash | ascii_downcase) as $s
                          | ($workflow | normalize_dash | ascii_downcase) as $w
                          | ($w + " / " + $s) as $ws
                          | ($n == $s
                             or $n == $ws
                             or ($n | endswith($s))
                             or ($n | contains($s)));
                        [.[] 
                         | select(match_subjob(.name; $workflow; $subjob))
                         | select(.status == "completed")
                         | (.run_attempt = (.run_attempt // $attempt))
                        ]' 2>/dev/null || echo "")
                if [ "$FILTERED" != "[]" ]; then
                    MATCHING_JOBS=$(jq -n --argjson acc "$MATCHING_JOBS" --argjson new "$FILTERED" '$acc + $new')
                fi

                if [ "$JOB_COUNT" -lt "$PER_PAGE" ]; then
                    break
                fi

                PAGE_J=$((PAGE_J + 1))
            done
            ATTEMPT=$((ATTEMPT - 1))
        done

        MATCH_COUNT=$(echo "$MATCHING_JOBS" | jq 'length' 2>/dev/null || echo "0")
        if [ "$MATCH_COUNT" -gt 0 ]; then
            FOUND_JOB=true
            SUBJOB_EVER_FOUND=true
            SORTED_JOBS=$(echo "$MATCHING_JOBS" | jq 'sort_by((.run_attempt // 0), (.completed_at // .started_at // .run_started_at // .created_at // ""))')
            mapfile -t SUBJOB_ROWS < <(echo "$SORTED_JOBS" | jq -c '.[]')
            for SUBJOB in "${SUBJOB_ROWS[@]}"; do
                    JOB_CONCLUSION=$(echo "$SUBJOB" | jq -r '.conclusion // "null"')
                    JOB_STATUS=$(echo "$SUBJOB" | jq -r '.status')
                    JOB_ID=$(echo "$SUBJOB" | jq -r '.id')
                    JOB_ATTEMPT=$(echo "$SUBJOB" | jq -r '.run_attempt // 1')
                    JOB_COMPLETED_AT=$(echo "$SUBJOB" | jq -r '.completed_at // empty')
                    JOB_URL="${BASE_URL}/actions/runs/${RUN_ID}/job/${JOB_ID}"

                    if [ "$JOB_STATUS" != "completed" ]; then
                        continue
                    fi

                    ENTRY_COMPLETED_AT="$RUN_COMPLETED_AT"
                    if [ -n "$JOB_COMPLETED_AT" ]; then
                        ENTRY_COMPLETED_AT="$JOB_COMPLETED_AT"
                    fi

                    if [ "$JOB_CONCLUSION" = "success" ]; then
                        if [ "$FOUND_SUCCESS" = false ]; then
                            LAST_SUCCESSFUL_RUN="$RUN_URL"
                            LAST_SUCCESSFUL_RUN_ID="$RUN_ID"
                            LAST_SUCCESSFUL_COMMIT="$RUN_COMMIT"
                            LAST_SUCCESSFUL_JOB_URL="$JOB_URL"
                            FOUND_SUCCESS=true
                            SUBJOB_RUNS_JSON=$(jq -n \
                                --arg status "success" \
                                --arg run_url "$RUN_URL" \
                                --arg job_url "$JOB_URL" \
                                --arg run_id "$RUN_ID" \
                                --arg job_id "$JOB_ID" \
                                --arg commit "$RUN_COMMIT" \
                                --arg completed_at "$ENTRY_COMPLETED_AT" \
                                --argjson job_attempt "$JOB_ATTEMPT" \
                                --argjson arr "$SUBJOB_RUNS_JSON" \
                                --argjson run_number "$PROCESSED" \
                                '$arr + [{status:$status, run_url:$run_url, job_url:$job_url, run_id:$run_id, job_id:$job_id, commit:$commit, completed_at:$completed_at, job_attempt:$job_attempt, run_number:$run_number}]' \
                            )

                            if [ -n "$MOST_RECENT_FAILURE_RUN" ]; then
                                FIRST_FAILING_RUN="$MOST_RECENT_FAILURE_RUN"
                                FIRST_FAILING_RUN_ID="$MOST_RECENT_FAILURE_RUN_ID"
                                FIRST_FAILING_COMMIT="$MOST_RECENT_FAILURE_COMMIT"
                                FIRST_FAILING_JOB_URL="$MOST_RECENT_FAILURE_JOB_URL"
                            fi

                            echo -e "${GREEN}✓ SUCCESS (last successful)${NC}"
                            echo ""
                            echo -e "${GREEN}Found last success and first failure - stopping search${NC}"
                            STOP_SEARCH=true
                            break
                        fi
                    elif [ "$JOB_CONCLUSION" = "failure" ]; then
                        MOST_RECENT_FAILURE_RUN="$RUN_URL"
                        MOST_RECENT_FAILURE_RUN_ID="$RUN_ID"
                        MOST_RECENT_FAILURE_COMMIT="$RUN_COMMIT"
                        MOST_RECENT_FAILURE_JOB_URL="$JOB_URL"
                        echo -e "${RED}✗ FAILURE${NC}"
                        FAILED_RUNS_JSON=$(jq -n \
                            --arg run_url "$RUN_URL" \
                            --arg job_url "$JOB_URL" \
                            --arg run_id "$RUN_ID" \
                            --arg job_id "$JOB_ID" \
                            --arg commit "$RUN_COMMIT" \
                            --arg completed_at "$ENTRY_COMPLETED_AT" \
                            --argjson job_attempt "$JOB_ATTEMPT" \
                            --arg conclusion "$JOB_CONCLUSION" \
                            --argjson arr "$FAILED_RUNS_JSON" \
                            --argjson run_number "$PROCESSED" \
                            '$arr + [{run_url:$run_url, job_url:$job_url, run_id:$run_id, job_id:$job_id, commit:$commit, completed_at:$completed_at, job_attempt:$job_attempt, conclusion:$conclusion, run_number:$run_number}]' \
                        )
                        SUBJOB_RUNS_JSON=$(jq -n \
                            --arg status "failure" \
                            --arg run_url "$RUN_URL" \
                            --arg job_url "$JOB_URL" \
                            --arg run_id "$RUN_ID" \
                            --arg job_id "$JOB_ID" \
                            --arg commit "$RUN_COMMIT" \
                            --arg completed_at "$ENTRY_COMPLETED_AT" \
                            --argjson job_attempt "$JOB_ATTEMPT" \
                            --argjson arr "$SUBJOB_RUNS_JSON" \
                            --argjson run_number "$PROCESSED" \
                            '$arr + [{status:$status, run_url:$run_url, job_url:$job_url, run_id:$run_id, job_id:$job_id, commit:$commit, completed_at:$completed_at, job_attempt:$job_attempt, run_number:$run_number}]' \
                        )
                        if [ "$FOUND_SUCCESS" = false ]; then
                            FAILURE_ONLY_COUNT=$((FAILURE_ONLY_COUNT + 1))
                            if [ "$FAILURE_ONLY_COUNT" -ge "$FAILURE_LIMIT" ]; then
                                echo -e "${YELLOW}Reached failure limit (${FAILURE_LIMIT}) without finding a successful run.${NC}"
                                EXCEEDED_FAILURE_LIMIT=true
                                STOP_SEARCH=true
                                break
                            fi
                        fi
                    else
                        echo -e "${YELLOW}Conclusion: ${JOB_CONCLUSION}${NC}"
                    fi
            done
        fi

        if [ "$FOUND_JOB" = false ]; then
            echo -e "${YELLOW}Subjob not found${NC}"
            # If we've scanned SUBJOB_MISSING_CANCEL_LIMIT runs on main without EVER
            # seeing this subjob, assume the subjob name is wrong and cancel early.
            if [ "$SUBJOB_EVER_FOUND" = false ] && [ "$PROCESSED" -ge "$SUBJOB_MISSING_CANCEL_LIMIT" ]; then
                write_cancel_and_exit "Subjob '${SUBJOB_NAME}' was not found in the first ${SUBJOB_MISSING_CANCEL_LIMIT} main-branch runs of workflow '${WORKFLOW_NAME}'. Please verify the job name."
            fi
            continue
        fi

        # If we've scanned 100 runs without a success but we did find at least one failure,
        # give up (same as 30 consecutive failures) so the LLM stops looking for a commit.
        if [ "$FOUND_SUCCESS" = false ] && [ "$PROCESSED" -ge "$RUN_LIMIT_WITHOUT_SUCCESS" ] && [ -n "$MOST_RECENT_FAILURE_RUN" ]; then
            echo -e "${YELLOW}Reached ${RUN_LIMIT_WITHOUT_SUCCESS} runs without finding a successful run (but saw failures). Stopping.${NC}"
            EXCEEDED_FAILURE_LIMIT=true
            STOP_SEARCH=true
        fi

        if [ "$STOP_SEARCH" = true ]; then
            break
        fi
    done

    if [ "$FOUND_SUCCESS" = true ] || [ "$STOP_SEARCH" = true ]; then
        break
    fi

    PAGE=$((PAGE + 1))

    if [ "$STOP_SEARCH" = true ]; then
        break
    fi
done

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}RESULTS${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

FOUND_FAILURE=false
if [ -n "$FIRST_FAILING_RUN" ]; then
    FOUND_FAILURE=true
fi

if [ "$FOUND_SUCCESS" = true ]; then
    echo -e "${GREEN}✓ LAST SUCCESSFUL RUN:${NC}"
    echo -e "  Run: ${LAST_SUCCESSFUL_RUN}"
    echo -e "  Run ID: ${LAST_SUCCESSFUL_RUN_ID}"
    echo -e "  Commit: ${LAST_SUCCESSFUL_COMMIT}"
    echo -e "  Commit URL: ${BASE_URL}/commit/${LAST_SUCCESSFUL_COMMIT}"
    echo ""
else
    echo -e "${YELLOW}⚠ No successful run found in analyzed runs${NC}"
    echo ""
fi

if [ "$FOUND_FAILURE" = true ]; then
    echo -e "${RED}✗ FIRST FAILING RUN:${NC}"
    echo -e "  Run: ${FIRST_FAILING_RUN}"
    echo -e "  Run ID: ${FIRST_FAILING_RUN_ID}"
    echo -e "  Commit: ${FIRST_FAILING_COMMIT}"
    echo -e "  Commit URL: ${BASE_URL}/commit/${FIRST_FAILING_COMMIT}"
    echo ""
else
    echo -e "${YELLOW}⚠ No failing run found in analyzed runs${NC}"
    echo ""
fi

COMPARE_URL=""
COMMIT_COUNT=""

if [ "$FOUND_SUCCESS" = true ] && [ "$FOUND_FAILURE" = true ]; then
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}COMMIT RANGE${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo -e "Commits between successful and failing runs:"
    COMPARE_URL="${BASE_URL}/compare/${LAST_SUCCESSFUL_COMMIT}...${FIRST_FAILING_COMMIT}"
    echo -e "  ${COMPARE_URL}"
    echo ""

    # Try to get commit count
    COMMIT_COUNT=$(git rev-list --count "${LAST_SUCCESSFUL_COMMIT}..${FIRST_FAILING_COMMIT}" 2>/dev/null || echo "unknown")
    echo -e "  Commit count: ${COMMIT_COUNT}"
    echo ""
fi

if [ "$FOUND_SUCCESS" = false ] && [ "$FOUND_FAILURE" = false ]; then
    if [ "$EXCEEDED_FAILURE_LIMIT" = true ]; then
        echo -e "${YELLOW}⚠ Failure limit reached without locating a successful run. Proceeding with fallback metadata.${NC}"
    else
        echo -e "${RED}Error: Could not find any runs with subjob '${SUBJOB_NAME}'${NC}"
        echo "Make sure the subjob name is correct and exists in the workflow."
        exit 1
    fi
fi

if [ "$EXCEEDED_FAILURE_LIMIT" = true ]; then
    BOUNDARY_STATUS="failure_limit_exceeded"
    BOUNDARY_MESSAGE="More than ${FAILURE_LIMIT} failed runs were scanned without finding a successful run. The commit window is too old—default to Case 2 or Case 3."
elif [ "$FOUND_SUCCESS" = false ]; then
    BOUNDARY_STATUS="no_success_found"
    BOUNDARY_MESSAGE="No successful runs were found within the current history window."
fi

if [ "$SUBJOB_RUNS_JSON" != "[]" ]; then
    SUBJOB_RUNS_JSON=$(echo "$SUBJOB_RUNS_JSON" | jq '
        def normalize(arr):
          arr | map(.completed_at = (.completed_at // ""));
        def assign_numbers(order):
          order
          | to_entries
          | map(.value + {run_number: .key});
        (normalize(.) | map(select(.status == "success")) | sort_by(.completed_at) | first) as $success
        | (normalize(.) | map(select(.status != "success")) | sort_by(.completed_at)) as $fails
        | if $success == null then
              assign_numbers(normalize(.) | sort_by(.completed_at))
          else
              assign_numbers([$success] + $fails)
          end
    ')
fi

FAILED_RUNS_JSON=$(echo "$SUBJOB_RUNS_JSON" | jq '[ .[] | select(.status != "success") ]')

if [ "$SUBJOB_RUNS_JSON" = "[]" ]; then
    echo -e "${BLUE}No qualifying subjob runs recorded.${NC}"
else
    echo -e "${BLUE}Recorded subjob runs (success + failure):${NC}"
    echo "$SUBJOB_RUNS_JSON"
fi

if [ "$FAILED_RUNS_JSON" = "[]" ]; then
    :
else
    echo -e "${BLUE}Failed subjobs (JSON subset):${NC}"
    echo "$FAILED_RUNS_JSON"
fi

if [ -n "$SUMMARY_JSON_PATH" ]; then
    tmp_summary="$(mktemp)"
    jq -n \
        --argjson runs "$SUBJOB_RUNS_JSON" \
        --arg status "$BOUNDARY_STATUS" \
        --arg message "$BOUNDARY_MESSAGE" \
        '{runs: $runs, status: $status, message: $message}' > "$tmp_summary"
    mv "$tmp_summary" "$SUMMARY_JSON_PATH"
    jq -n \
        --argjson runs "$SUBJOB_RUNS_JSON" \
        --arg status "$BOUNDARY_STATUS" \
        --arg message "$BOUNDARY_MESSAGE" \
        '{runs: $runs, status: $status, message: $message}' > "$RUNS_JSON_PATH"
fi

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
# workflow_finder uses AT_OWNER_REPO from config; ensure it matches find_boundaries' REPO
export AT_OWNER_REPO="${AT_OWNER_REPO:-$REPO}"
export AUTO_TRIAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=modules/boundaries/workflow_finder.sh
source "$AUTO_TRIAGE_ROOT/modules/boundaries/workflow_finder.sh"
# shellcheck source=modules/boundaries/run_processor.sh
source "$AUTO_TRIAGE_ROOT/modules/boundaries/run_processor.sh"

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

# Find the workflow ID (support both .yaml and .yml)
echo "Finding workflow ID..."
WORKFLOW_ID=$(find_workflow_id "$WORKFLOW_NAME") || true

if [ -z "$WORKFLOW_ID" ]; then
    echo -e "${RED}Error: Could not find workflow '${WORKFLOW_NAME}' with .yaml or .yml extension${NC}"
    echo "Make sure the workflow file exists at: .github/workflows/${WORKFLOW_NAME}.yaml (or .yml)"
    # Instead of hard failing the whole job, signal a graceful cancellation so
    # the auto-triage action can send a Slack message explaining what happened.
    write_cancel_and_exit "Workflow '${WORKFLOW_NAME}' not found in repository ${REPO}. Verify file path."
fi

echo -e "${GREEN}Found workflow ID: ${WORKFLOW_ID}${NC}"
echo ""

# Fetch and process workflow runs (pagination, filtering, job matching)
echo "Processing workflow runs page by page (this may take some time)..."
PER_PAGE=100
FAILURE_LIMIT=30
RUN_LIMIT_WITHOUT_SUCCESS=100
SUBJOB_MISSING_CANCEL_LIMIT=50

process_workflow_runs

# run_processor sets all outputs; derive FOUND_FAILURE for display
FOUND_FAILURE=false
[ -z "$FIRST_FAILING_RUN" ] || FOUND_FAILURE=true

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}RESULTS${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

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

# BOUNDARY_STATUS and BOUNDARY_MESSAGE set by run_processor

if [ "$SUBJOB_RUNS_JSON" = "[]" ]; then
    echo -e "${BLUE}No qualifying subjob runs recorded.${NC}"
else
    echo -e "${BLUE}Recorded subjob runs (success + failure):${NC}"
    echo "$SUBJOB_RUNS_JSON"
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

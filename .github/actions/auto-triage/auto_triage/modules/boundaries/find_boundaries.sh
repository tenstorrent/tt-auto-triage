#!/bin/bash
#
# Find the last successful and first failing run of a specific subjob.
# Orchestrates workflow_finder and run_processor. Uses lib/config.sh.
# Entry point: invoked by action.yml, filter_triage, auto_triage.
#

set -euo pipefail

_MOD_FB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/config.sh
source "$_MOD_FB_DIR/../../lib/config.sh"
# shellcheck source=workflow_finder.sh
source "$_MOD_FB_DIR/workflow_finder.sh"
# shellcheck source=run_processor.sh
source "$_MOD_FB_DIR/run_processor.sh"

CUTOFF_COMMIT="${CUTOFF_COMMIT:-}"

if [ $# -lt 2 ]; then
    log_error "Missing required arguments"
    echo "Usage: $0 <workflow_name> <subjob_name>"
    echo ""
    echo "Examples:"
    echo "  $0 single-card-demo-tests yolov5x-N150-func"
    echo "  $0 single-card-demo-tests vanilla_unet-N150-func"
    exit 1
fi

WORKFLOW_NAME="$1"
SUBJOB_NAME="$2"

# assumes python3 is available and at version 3.12.3 (the default in Ubuntu 24.04)
normalize_hyphens() {
    python3 - "$1" <<'PY'
import sys, unicodedata
text = sys.argv[1]
print(''.join('-' if unicodedata.category(ch) == 'Pd' else ch for ch in text), end='')
PY
}

WORKFLOW_NAME=$(normalize_hyphens "$WORKFLOW_NAME")
SUBJOB_NAME=$(normalize_hyphens "$SUBJOB_NAME")

export AT_OWNER_REPO="${AT_OWNER_REPO}"
REPO="$AT_OWNER_REPO"
BASE_URL="$AT_BASE_URL"

DATA_DIR="$(get_data_dir)"
SUBJOB_RUNS_JSON_PATH="${DATA_DIR}/subjob_runs.json"
CANCEL_FILE="data/config/cancel.json"

write_cancel_and_exit() {
    local message="$1"
    tmp_cancel="$(mktemp)"
    jq -n --arg msg "$message" '{should_cancel: true, message: $msg}' > "$tmp_cancel"
    mv "$tmp_cancel" "$CANCEL_FILE"
    log_warn "$message"
    log_warn "Created ${CANCEL_FILE}; downstream stages will treat this as a cancellation."
    exit 0
}

mkdir -p "$DATA_DIR"
rm -f "$SUBJOB_RUNS_JSON_PATH"

log_info "Searching for workflow: ${WORKFLOW_NAME}"
log_info "Looking for subjob: ${SUBJOB_NAME}"

if [ -n "$CUTOFF_COMMIT" ]; then
    log_warn "========================================"
    log_warn "TESTING MODE: Cutoff commit filter active"
    log_warn "Ignoring all runs on commits newer than: ${CUTOFF_COMMIT}"
    log_warn "========================================"
fi
echo ""

log_info "Finding workflow ID..."
WORKFLOW_ID=$(find_workflow_id "$WORKFLOW_NAME") || true

if [ -z "$WORKFLOW_ID" ]; then
    log_error "Could not find workflow '${WORKFLOW_NAME}' with .yaml or .yml extension"
    echo "Make sure the workflow file exists at: .github/workflows/${WORKFLOW_NAME}.yaml (or .yml)"
    write_cancel_and_exit "Workflow '${WORKFLOW_NAME}' not found in repository ${REPO}. Verify file path."
fi

log_success "Found workflow ID: ${WORKFLOW_ID}"
echo ""

log_info "Processing workflow runs page by page (this may take some time)..."
export WORKFLOW_ID SUBJOB_NAME WORKFLOW_NAME REPO BASE_URL
export CUTOFF_COMMIT
export PER_PAGE="${AT_PER_PAGE}"
export FAILURE_LIMIT="${AT_FAILURE_LIMIT}"
export RUN_LIMIT_WITHOUT_SUCCESS="${AT_RUN_LIMIT_WITHOUT_SUCCESS}"
export SUBJOB_MISSING_CANCEL_LIMIT="${AT_SUBJOB_MISSING_CANCEL_LIMIT}"

process_workflow_runs

echo ""
log_info "========================================"
log_info "RESULTS"
log_info "========================================"
echo ""

if [ "$FOUND_SUCCESS" = true ]; then
    log_success "✓ LAST SUCCESSFUL RUN:"
    echo "  Run: ${LAST_SUCCESSFUL_RUN}"
    echo "  Run ID: ${LAST_SUCCESSFUL_RUN_ID}"
    echo "  Commit: ${LAST_SUCCESSFUL_COMMIT}"
    echo "  Commit URL: ${BASE_URL}/commit/${LAST_SUCCESSFUL_COMMIT}"
    echo ""
else
    log_warn "⚠ No successful run found in analyzed runs"
    echo ""
fi

if [ "$FOUND_FAILURE" = true ]; then
    log_error "✗ FIRST FAILING RUN:"
    echo "  Run: ${FIRST_FAILING_RUN}"
    echo "  Run ID: ${FIRST_FAILING_RUN_ID}"
    echo "  Commit: ${FIRST_FAILING_COMMIT}"
    echo "  Commit URL: ${BASE_URL}/commit/${FIRST_FAILING_COMMIT}"
    echo ""
else
    log_warn "⚠ No failing run found in analyzed runs"
    echo ""
fi

if [ "$FOUND_SUCCESS" = true ] && [ "$FOUND_FAILURE" = true ]; then
    log_info "========================================"
    log_info "COMMIT RANGE"
    log_info "========================================"
    echo ""
    echo "Commits between successful and failing runs:"
    COMPARE_URL="${BASE_URL}/compare/${LAST_SUCCESSFUL_COMMIT}...${FIRST_FAILING_COMMIT}"
    echo "  ${COMPARE_URL}"
    echo ""
    COMMIT_COUNT=$(git rev-list --count "${LAST_SUCCESSFUL_COMMIT}..${FIRST_FAILING_COMMIT}" 2>/dev/null || echo "unknown")
    echo "  Commit count: ${COMMIT_COUNT}"
    echo ""
fi

if [ "$FOUND_SUCCESS" = false ] && [ "$FOUND_FAILURE" = false ]; then
    if [ "$EXCEEDED_FAILURE_LIMIT" = true ]; then
        log_warn "⚠ Failure limit reached without locating a successful run. Proceeding with fallback metadata."
    else
        log_error "Could not find any runs with subjob '${SUBJOB_NAME}'"
        echo "Make sure the subjob name is correct and exists in the workflow."
        exit 1
    fi
fi

if [ "$SUBJOB_RUNS_JSON" = "[]" ]; then
    log_info "No qualifying subjob runs recorded."
else
    log_info "Recorded subjob runs (success + failure):"
    echo "$SUBJOB_RUNS_JSON"
fi

jq -n \
    --argjson runs "$SUBJOB_RUNS_JSON" \
    --arg status "$BOUNDARY_STATUS" \
    --arg message "$BOUNDARY_MESSAGE" \
    '{runs: $runs, status: $status, message: $message}' > "$SUBJOB_RUNS_JSON_PATH"

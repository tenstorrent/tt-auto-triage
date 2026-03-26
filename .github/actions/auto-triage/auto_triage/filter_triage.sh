#!/bin/bash
#
# Filter stage driver: determines deterministic failures, gathers commits, and identifies commit ranges.
# Usage:
#   ./filter_triage.sh <workflow_name> <subjob_name> [ci-mode]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/config.sh
source "$SCRIPT_DIR/lib/config.sh"
# shellcheck source=modules/analysis/llm_runner.sh
source "$SCRIPT_DIR/modules/analysis/llm_runner.sh"

if [ $# -lt 2 ]; then
    log_error "Usage: $0 <workflow_name> <subjob_name> [ci-mode]"
    exit 1
fi

WORKFLOW="$1"
SUBJOB="$2"
CI_MODE="${3:-}"

ROOT="$AUTO_TRIAGE_ROOT"
FIND_SCRIPT="${ROOT}/modules/boundaries/find_boundaries.sh"

log_info "Filter stage: preparing directories"
setup_triage_dirs "$ROOT"

if [ "$CI_MODE" = "ci" ]; then
    log_info "Filter stage CI mode detected, removing find_boundaries to prevent re-execution."
    rm -f "$FIND_SCRIPT"
fi

log_info "Verifying boundary artifacts for filter stage"
SUBJOB_RUNS_FILE="${CANON_DATA_DIR}/subjob_runs.json"
if [ ! -s "$SUBJOB_RUNS_FILE" ]; then
    log_error "Boundary metadata missing (expected at ${SUBJOB_RUNS_FILE})."
    ls -l "$CANON_DATA_DIR"
    exit 1
fi

FILTER_BASE="${ROOT}/instructions/filter_instructions_for_llm.txt"
FILTER_HANG="${ROOT}/instructions/filter_hang_instructions_for_llm.txt"
if [ ! -f "$FILTER_BASE" ] || [ ! -f "$FILTER_HANG" ]; then
    log_error "Filter instructions missing: need $FILTER_BASE and $FILTER_HANG"
    exit 1
fi

FILTER_MERGED=$(mktemp)
cat "$FILTER_BASE" "$FILTER_HANG" > "$FILTER_MERGED"

log_info "Launching GitHub Copilot CLI filter stage"
if ! run_llm_analysis "$FILTER_MERGED" "$WORKFLOW" "$SUBJOB" "$CI_MODE"; then
    rm -f "$FILTER_MERGED"
    exit 1
fi
rm -f "$FILTER_MERGED"

# De-duplicate commit_info.json entries after the filter LLM has finished.
# This ensures that any overlapping batches or manual backfills in the filter
# stage do not cause the main analysis LLM to see duplicate commits.
COMMIT_FILE="${CANON_DATA_DIR}/commit_info.json"
if [ -f "$COMMIT_FILE" ]; then
    # Only attempt de-duplication when the file is a JSON array. If it is a
    # string (e.g., "too many commits" fallback), leave it untouched.
    if jq -e 'type == "array"' "$COMMIT_FILE" >/dev/null 2>&1; then
        log_info "De-duplicating commit_info.json entries (filter stage)"
        TMP_COMMIT_FILE="$(mktemp)"
        if jq 'unique_by(.commit // .commit_short // .commit_sha // "")' "$COMMIT_FILE" > "$TMP_COMMIT_FILE" 2>/dev/null; then
            mv "$TMP_COMMIT_FILE" "$COMMIT_FILE"
        else
            log_warn "Failed to de-duplicate commit_info.json; leaving original file unchanged."
            rm -f "$TMP_COMMIT_FILE" || true
        fi
    fi
fi

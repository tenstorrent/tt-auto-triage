#!/bin/bash
#
# Filter stage: deterministic error + commit window. Prompt = filter.fragments (incl. hang download).
# Usage: ./filter_triage.sh <workflow_name> <subjob_name> [ci-mode]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/config.sh
source "$SCRIPT_DIR/lib/config.sh"
# shellcheck source=modules/analysis/llm_runner.sh
source "$SCRIPT_DIR/modules/analysis/llm_runner.sh"
# shellcheck source=lib/instructions_pipeline.sh
source "$SCRIPT_DIR/lib/instructions_pipeline.sh"

if [ $# -lt 2 ]; then
    log_error "Usage: $0 <workflow_name> <subjob_name> [ci-mode]"
    exit 1
fi

WORKFLOW="$1"
SUBJOB="$2"
CI_MODE="${3:-}"
ROOT="$AUTO_TRIAGE_ROOT"
FIND_SCRIPT="${ROOT}/modules/boundaries/find_boundaries.sh"

log_info "Filter stage: setup"
setup_triage_dirs "$ROOT"

if [ "$CI_MODE" = "ci" ]; then
    log_info "CI mode: skip re-running find_boundaries"
    rm -f "$FIND_SCRIPT"
fi

SUBJOB_RUNS_FILE="${CANON_DATA_DIR}/subjob_runs.json"
if [ ! -s "$SUBJOB_RUNS_FILE" ]; then
    log_error "Boundary metadata missing: $SUBJOB_RUNS_FILE"
    ls -l "$CANON_DATA_DIR"
    exit 1
fi

FILTER_MERGED=$(mktemp)
if ! build_instruction_bundle "$FILTER_MERGED" "$ROOT" "$AT_PIPELINE_FILTER_FRAGMENTS"; then
    rm -f "$FILTER_MERGED"
    exit 1
fi

log_info "Copilot: filter pass"
if ! run_llm_analysis "$FILTER_MERGED" "$WORKFLOW" "$SUBJOB" "$CI_MODE"; then
    rm -f "$FILTER_MERGED"
    exit 1
fi
rm -f "$FILTER_MERGED"

COMMIT_FILE="${CANON_DATA_DIR}/commit_info.json"
if [ -f "$COMMIT_FILE" ] && jq -e 'type == "array"' "$COMMIT_FILE" &>/dev/null; then
    log_info "De-duplicating commit_info.json"
    TMP="$(mktemp)"
    if jq 'unique_by(.commit // .commit_short // .commit_sha // "")' "$COMMIT_FILE" >"$TMP" 2>/dev/null; then
        mv "$TMP" "$COMMIT_FILE"
    else
        rm -f "$TMP"
        log_warn "commit_info.json de-dupe skipped"
    fi
fi

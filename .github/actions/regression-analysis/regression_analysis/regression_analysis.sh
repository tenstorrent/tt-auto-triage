#!/bin/bash
#
# Main triage: commit/case analysis, then optional follow-ups (e.g. hang — followups.manifest).
# Usage: ./regression_analysis.sh <workflow_name> <subjob_name> [ci-mode]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/config.sh
source "$SCRIPT_DIR/lib/config.sh"
# shellcheck source=lib/hang_detect.sh
source "$SCRIPT_DIR/lib/hang_detect.sh"
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
ROOT="$REGRESSION_ANALYSIS_ROOT"

log_info "Preparing regression_analysis/data and regression_analysis/logs"
setup_triage_dirs "$ROOT"
rm -rf "$(get_output_dir "$ROOT")"
mkdir -p "$(get_output_dir "$ROOT")"
cd "$ROOT"

log_info "Verifying boundary artifacts"
SUBJOB_RUNS_FILE="${CANON_DATA_DIR}/subjob_runs.json"
if [ ! -s "$SUBJOB_RUNS_FILE" ]; then
    log_error "subjob_runs.json not found at $SUBJOB_RUNS_FILE"
    ls -l "$CANON_DATA_DIR"
    exit 1
fi
SUMMARY_COUNT=$(jq 'if type=="array" then length else ((.runs // []) | length) end' "$SUBJOB_RUNS_FILE")
FAIL_COUNT=$(jq 'if type=="array"
                 then ([.[] | select(.status != "success")] | length)
                 else ((.runs // []) | map(select(.status != "success")) | length)
                 end' "$SUBJOB_RUNS_FILE")
log_info "runs recorded: $SUMMARY_COUNT, failures: $FAIL_COUNT"

PROMPT_FILE=$(mktemp)
trap 'rm -f "$PROMPT_FILE"' EXIT

build_instruction_bundle "$PROMPT_FILE" "$ROOT" "$AT_PIPELINE_MAIN_FRAGMENTS" || exit 1

log_info "Copilot: main triage pass"
run_llm_analysis "$PROMPT_FILE" "$WORKFLOW" "$SUBJOB" "$CI_MODE" || exit $?

trap - EXIT
rm -f "$PROMPT_FILE"

run_instruction_followups "$ROOT" "$WORKFLOW" "$SUBJOB" "$CI_MODE" "$AT_PIPELINE_FOLLOWUPS_MANIFEST"

VERIFY_SCRIPT="${ROOT}/verify_commit_metadata.sh"
if [ -x "$VERIFY_SCRIPT" ] && ! "$VERIFY_SCRIPT"; then
    exit 1
fi

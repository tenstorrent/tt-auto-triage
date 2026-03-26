#!/bin/bash
#
# Full triage driver: analyzes filtered commits and produces triage reports.
# Usage:
#   ./auto_triage.sh <workflow_name> <subjob_name> [ci-mode]
# Example:
#   ./auto_triage.sh galaxy-quick quick-wh-glx-quick
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/config.sh
source "$SCRIPT_DIR/lib/config.sh"
# shellcheck source=lib/followup_triggers.sh
source "$SCRIPT_DIR/lib/followup_triggers.sh"
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

log_info "Preparing auto_triage/data and auto_triage/logs"
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
log_info "runs recorded: $SUMMARY_COUNT"
log_info "failures recorded: $FAIL_COUNT"

PROMPT_FILE=$(mktemp)
cleanup_prompt() { rm -f "$PROMPT_FILE"; }
trap cleanup_prompt EXIT

if ! build_instruction_bundle "$PROMPT_FILE" "$ROOT" "instructions/pipelines/main.fragments"; then
    trap - EXIT
    rm -f "$PROMPT_FILE"
    exit 1
fi

log_info "Launching GitHub Copilot CLI (main triage pass)"
run_llm_analysis "$PROMPT_FILE" "$WORKFLOW" "$SUBJOB" "$CI_MODE" || exit $?
trap - EXIT
rm -f "$PROMPT_FILE"

run_instruction_followups "$ROOT" "$WORKFLOW" "$SUBJOB" "$CI_MODE" "instructions/pipelines/followups.manifest"

VERIFY_SCRIPT="${ROOT}/verify_commit_metadata.sh"
if [ -x "$VERIFY_SCRIPT" ]; then
    if ! "$VERIFY_SCRIPT"; then
        exit 1
    fi
fi

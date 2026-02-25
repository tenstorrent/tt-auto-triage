#!/bin/bash
#
# llm_runner.sh - Run LLM analysis via GitHub Copilot CLI
#
# Provides: run_llm_analysis(instructions_file, workflow, job, mode)
# - instructions_file: path to instructions text (e.g. filter_instructions_for_llm.txt)
# - workflow: workflow name for prompt context
# - job: subjob name for prompt context
# - mode: "ci" for CI environment (no interactive approval)
#
# Returns: exit code from copilot invocation
# Uses lib/common.sh
#
# Usage: source this file, then call run_llm_analysis.
#

if [ -n "${_LLM_RUNNER_LOADED:-}" ]; then
    return 0
fi
_LLM_RUNNER_LOADED=1

_LR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "$_LR_DIR/../../lib/common.sh"

# Run LLM analysis with Copilot CLI.
# Returns the exit code from copilot.
#
#   run_llm_analysis "$instructions_file" "workflow" "job" "ci"
#   rc=$?
#
run_llm_analysis() {
    local instructions_file="$1"
    local workflow="$2"
    local job="$3"
    local mode="${4:-}"

    if [ ! -f "$instructions_file" ]; then
        log_error "Instructions file not found: $instructions_file"
        return 1
    fi

    check_command copilot

    if [ -z "${COPILOT_GITHUB_TOKEN:-}" ]; then
        log_warn "COPILOT_GITHUB_TOKEN not set, falling back to GH_TOKEN"
        export COPILOT_GITHUB_TOKEN="${GH_TOKEN:-}"
    fi

    local prompt
    prompt=$(printf 'You are operating in a CI environment with no interactive approval. Complete the following instructions for workflow '\''%s'\'' and job '\''%s'\'':\n\n%s' \
        "$workflow" "$job" "$(cat "$instructions_file")")

    copilot -p "$prompt" --allow-all-tools
    return $?
}

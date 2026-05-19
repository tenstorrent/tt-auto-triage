#!/bin/bash
#
# result_comparator.sh - Compare retry vs original errors using Copilot (LLM)
#
# Uses Copilot to compare error messages. Expects original_error.txt and
# retry_error.txt in data_dir (written by caller). Copilot writes
# error_comparison.json with same_failure, retry_error_extracted.
#
# Provides:
#   run_copilot_error_comparison(root, data_dir) -> 0 on success
#   get_same_failure_from_comparison(comparison_file) -> "true"|"false"
#   get_retry_error_extracted(comparison_file) -> extracted error text
#   determine_retry_result(retry_status, same_failure) -> result_type
#
# Result types: passed, failed_same, failed_different
# Uses lib/common.sh
#
# Usage: source this file.
#

if [ -n "${_RESULT_COMPARATOR_LOADED:-}" ]; then
    return 0
fi
_RESULT_COMPARATOR_LOADED=1

_MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_MODULE_DIR}/../../lib"
# shellcheck source=../../lib/common.sh
[ -f "${_LIB_DIR}/common.sh" ] && source "${_LIB_DIR}/common.sh"

# ==============================================================================
# run_copilot_error_comparison(root, data_dir) -> 0 on success, 1 on failure
#
# Invokes Copilot with compare_errors_instructions.txt. Expects
# original_error.txt and retry_error.txt to exist in data_dir.
# Writes error_comparison.json to data_dir on success.
# ==============================================================================
run_copilot_error_comparison() {
    local root="${1:-}"
    local data_dir="${2:-}"

    if [ -z "$root" ] || [ -z "$data_dir" ]; then
        log_error "run_copilot_error_comparison: root and data_dir required"
        return 1
    fi

    local instructions_path="${root}/instructions/compare_errors_instructions.txt"
    if [ ! -f "$instructions_path" ]; then
        log_error "instructions/compare_errors_instructions.txt not found at ${instructions_path}"
        return 1
    fi

    if [ ! -f "${data_dir}/original_error.txt" ] || [ ! -f "${data_dir}/retry_error.txt" ]; then
        log_error "run_copilot_error_comparison: original_error.txt and retry_error.txt must exist in data_dir"
        return 1
    fi

    local compare_prompt
    compare_prompt=$(printf 'You are operating in a CI environment. Compare two error messages and determine if they represent the same failure.\n\n%s' "$(cat "$instructions_path")")

    # Ensure COPILOT_GITHUB_TOKEN is set (Copilot uses it for GitHub context)
    if [ -z "${COPILOT_GITHUB_TOKEN:-}" ]; then
        export COPILOT_GITHUB_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
    fi

    # Export for mock copilot (tests can inject a script that reads this)
    export RESULT_COMPARATOR_DATA_DIR="$data_dir"

    local saved_pwd
    saved_pwd=$(pwd)
    cd "$root" || return 1
    copilot -p "$compare_prompt" --allow-all-tools 2>/dev/null || true
    cd "$saved_pwd" || true

    if [ -f "${data_dir}/error_comparison.json" ]; then
        return 0
    fi
    log_warn "Copilot did not produce error_comparison.json; assuming different failures"
    return 1
}

# ==============================================================================
# get_same_failure_from_comparison(comparison_file) -> "true"|"false"
# ==============================================================================
get_same_failure_from_comparison() {
    local file="${1:-}"
    if [ -z "$file" ] || [ ! -f "$file" ]; then
        echo "false"
        return 0
    fi
    jq -r '.same_failure // false' "$file" 2>/dev/null || echo "false"
}

# ==============================================================================
# get_retry_error_extracted(comparison_file) -> extracted error text (or "")
# ==============================================================================
get_retry_error_extracted() {
    local file="${1:-}"
    if [ -z "$file" ] || [ ! -f "$file" ]; then
        echo ""
        return 0
    fi
    jq -r '.retry_error_extracted // ""' "$file" 2>/dev/null || echo ""
}

# ==============================================================================
# determine_retry_result(retry_status, same_failure) -> result_type
#
# Returns: passed | failed_same | failed_different
# ==============================================================================
determine_retry_result() {
    local retry_status="${1:-}"
    local same_failure="${2:-false}"

    if [ "$retry_status" = "success" ]; then
        echo "passed"
        return 0
    fi

    if [ "$same_failure" = "true" ]; then
        echo "failed_same"
    else
        echo "failed_different"
    fi
}

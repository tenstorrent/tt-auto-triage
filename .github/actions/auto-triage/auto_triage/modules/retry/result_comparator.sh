#!/bin/bash
#
# result_comparator.sh - Compare retry vs original errors and determine result type
#
# Provides:
#   compare_errors(original_error, retry_error) -> similarity_score (0-100)
#   determine_retry_result(original_status, retry_status, error_similarity) -> result_type
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

# Threshold above which errors are considered "same"
RESULT_COMPARATOR_SAME_THRESHOLD="${RESULT_COMPARATOR_SAME_THRESHOLD:-70}"

# ==============================================================================
# Normalize error text for comparison (lowercase, collapse whitespace)
# ==============================================================================
_result_comparator_normalize() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | tr -s ' \t\n\r' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# ==============================================================================
# Extract significant words (alphanumeric, underscores, hyphens)
# ==============================================================================
_result_comparator_words() {
    echo "$1" | tr -cs '[:alnum:]_-' '\n' | grep -v '^$' | sort -u
}

# ==============================================================================
# compare_errors(original_error, retry_error) -> similarity_score (0-100)
#
# Heuristic comparison using substring containment and word overlap.
# For LLM-based comparison, the caller can use a different path (e.g. Copilot)
# and pass the result into determine_retry_result.
# ==============================================================================
compare_errors() {
    local orig="${1:-}"
    local retry="${2:-}"

    if [ -z "$orig" ] || [ -z "$retry" ]; then
        echo "0"
        return 0
    fi

    local orig_norm retry_norm
    orig_norm=$(_result_comparator_normalize "$orig")
    retry_norm=$(_result_comparator_normalize "$retry")

    # Substring: if one contains the other (longer contains shorter), high score
    if [ ${#orig_norm} -gt ${#retry_norm} ]; then
        if [[ "$orig_norm" == *"$retry_norm"* ]] && [ ${#retry_norm} -gt 20 ]; then
            echo "85"
            return 0
        fi
    else
        if [[ "$retry_norm" == *"$orig_norm"* ]] && [ ${#orig_norm} -gt 20 ]; then
            echo "85"
            return 0
        fi
    fi

    # Word overlap: Jaccard-like = |intersection| / min(|a|, |b|) * 100
    local orig_words retry_words common
    orig_words=$(_result_comparator_words "$orig_norm")
    retry_words=$(_result_comparator_words "$retry_norm")

    common=$(comm -12 <(echo "$orig_words") <(echo "$retry_words") 2>/dev/null | wc -l | tr -d ' ')
    local count_orig count_retry
    count_orig=$(echo "$orig_words" | wc -l | tr -d ' ')
    count_retry=$(echo "$retry_words" | wc -l | tr -d ' ')

    if [ "$count_orig" -eq 0 ] || [ "$count_retry" -eq 0 ]; then
        echo "0"
        return 0
    fi

    local min_count
    min_count=$(( count_orig < count_retry ? count_orig : count_retry ))
    local score
    score=$(( common * 100 / min_count ))
    [ $score -gt 100 ] && score=100
    echo "$score"
}

# ==============================================================================
# determine_retry_result(original_status, retry_status, error_similarity) -> result_type
#
# Returns: passed | failed_same | failed_different
# ==============================================================================
determine_retry_result() {
    local original_status="${1:-}"
    local retry_status="${2:-}"
    local error_similarity="${3:-0}"

    # Retry passed -> non-deterministic
    if [ "$retry_status" = "success" ]; then
        echo "passed"
        return 0
    fi

    # Retry failed: use similarity to decide same vs different
    local threshold="${RESULT_COMPARATOR_SAME_THRESHOLD:-70}"
    if [ "$error_similarity" -ge "$threshold" ]; then
        echo "failed_same"
    else
        echo "failed_different"
    fi
}

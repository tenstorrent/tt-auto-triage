#!/bin/bash
#
# commit_validator.sh - Validate commit metadata against GitHub API data
#
# Provides validate_commit_metadata(entry_json, context_file, index).
# Checks author, approvers, commit_url match GitHub.
# Returns 0 if valid, 1 if invalid (logs reason on stderr).
#
# Usage: source this file, then call validate_commit_metadata
#

if [ -n "${_COMMIT_VALIDATOR_LOADED:-}" ]; then
    return 0
fi
_COMMIT_VALIDATOR_LOADED=1

_CV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/github_api.sh
source "$_CV_DIR/../../lib/github_api.sh"

_validation_fail() {
    local reason="$1" context="$2" index="${3:-n/a}" commit="${4:-<none>}"
    echo "detected hallucination: $reason" >&2
    echo "  context_file: $context" >&2
    echo "  entry_index: $index" >&2
    echo "  commit: $commit" >&2
    return 1
}

# Validate a single commit metadata entry against GitHub.
# Returns 0 if valid, 1 if invalid.
#
#   validate_commit_metadata "$entry_json" "chosen_commit.json" 0
#
validate_commit_metadata() {
    local entry_json="$1"
    local context_file="${2:-unknown}"
    local index="${3:-n/a}"

    local commit
    commit=$(echo "$entry_json" | jq -r '.commit // .commit_sha // empty')
    if [ -z "$commit" ]; then
        _validation_fail "missing commit SHA" "$context_file" "$index" ""
        return 1
    fi

    local expected_url="https://github.com/${AT_OWNER_REPO}/commit/${commit}"
    local declared_url
    declared_url=$(echo "$entry_json" | jq -r '.commit_url // empty')
    if [ -n "$declared_url" ] && [ "$declared_url" != "$expected_url" ]; then
        _validation_fail "commit_url mismatch" "$context_file" "$index" "$commit"
        return 1
    fi

    local decl_authors_str decl_approvers_str
    decl_authors_str=$(echo "$entry_json" | jq -r '.authors[]?.login // empty' 2>/dev/null | sort -u)
    decl_approvers_str=$(echo "$entry_json" | jq -r '.approvers[]?.login // empty' 2>/dev/null | sort -u)

    local actual_author
    actual_author=$(get_commit_author "$commit")
    if [ -n "$actual_author" ]; then
        if ! echo "$decl_authors_str" | grep -Fxq "$actual_author"; then
            _validation_fail "commit author missing from authors[]" "$context_file" "$index" "$commit"
            return 1
        fi
    fi

    if [ -z "$decl_authors_str" ]; then
        local msg="authors[] empty and author unresolved"
        [ -n "$actual_author" ] && msg="authors[] empty but GitHub returned author"
        _validation_fail "$msg" "$context_file" "$index" "$commit"
        return 1
    fi

    local pr_number
    pr_number=$(get_pr_for_commit "$commit")
    local actual_approvers
    actual_approvers=""
    if [ -n "$pr_number" ]; then
        actual_approvers=$(get_pr_approvers "$pr_number")
    fi

    if [ -n "$actual_approvers" ]; then
        if [ -z "$decl_approvers_str" ]; then
            _validation_fail "approvers[] empty but GitHub shows approvals" "$context_file" "$index" "$commit"
            return 1
        fi
        while IFS= read -r decl; do
            [ -z "$decl" ] && continue
            if ! echo "$actual_approvers" | grep -Fxq "$decl"; then
                _validation_fail "approver '$decl' not found in GitHub approvals" "$context_file" "$index" "$commit"
                return 1
            fi
        done <<< "$decl_approvers_str"
    else
        if [ -n "$decl_approvers_str" ]; then
            _validation_fail "approvers listed but GitHub shows none" "$context_file" "$index" "$commit"
            return 1
        fi
    fi

    return 0
}

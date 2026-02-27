#!/bin/bash
#
# validation.sh - Input and output validation helpers for auto-triage
#
# Provides reusable checks that are currently scattered (and duplicated)
# across find_boundaries.sh, download_*.sh, get_logs.sh, etc.
#
# Prerequisites: none (sources common.sh for die/warn).
# Usage: source this file (it pulls in common.sh automatically).
#

if [ -n "${_AUTO_TRIAGE_VALIDATION_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_VALIDATION_LOADED=1

# validation.sh depends on common.sh (die, warn).
_VALIDATION_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$_VALIDATION_LIB_DIR/common.sh"

# ==============================================================================
# Commit SHA validation
# ==============================================================================

# Verify that a string looks like a full or abbreviated commit SHA and that
# git recognises it in the current repository.  Dies on failure.
#
#   validate_commit_sha "$sha"
#   validate_commit_sha "$sha" "Start commit"
#
validate_commit_sha() {
    local sha="$1" label="${2:-Commit}"
    if [ -z "$sha" ]; then
        die "$label SHA is empty"
    fi
    # Ensure the value looks like a full or abbreviated SHA (7-40 hex chars).
    if ! is_valid_sha_format "$sha"; then
        die "$label '$sha' is not a valid commit SHA format"
    fi
    # Ensure git recognises it as a commit object in the current repository.
    if ! git rev-parse --verify "${sha}^{commit}" >/dev/null 2>&1; then
        die "$label '$sha' not found in repository as a commit"
    fi
}

# Lighter check: does the string look like a hex SHA (7-40 chars)?
# Returns 0/1, does not die.
#
#   if is_valid_sha_format "$input"; then ...
#
is_valid_sha_format() {
    [[ "$1" =~ ^[0-9a-f]{7,40}$ ]]
}

# ==============================================================================
# GitHub job URL parsing
# ==============================================================================

# Parse a GitHub Actions job URL into its components.
# Sets the caller's variables: _owner, _repo, _run_id, _job_id.
# Returns 1 if the URL doesn't match the expected pattern.
#
#   if parse_job_url "$url"; then
#       echo "$_owner $_repo $_run_id $_job_id"
#   fi
#
parse_job_url() {
    local url="$1"
    unset _owner _repo _run_id _job_id 2>/dev/null || true
    if [[ "$url" =~ github\.com/([^/]+)/([^/]+)/actions/runs/([0-9]+)/job/([0-9]+) ]]; then
        _owner="${BASH_REMATCH[1]}"
        _repo="${BASH_REMATCH[2]}"
        _run_id="${BASH_REMATCH[3]}"
        _job_id="${BASH_REMATCH[4]}"
        return 0
    fi
    return 1
}

# ==============================================================================
# JSON file validation
# ==============================================================================

# Assert that a file exists, is non-empty, and contains valid JSON.
# Dies on failure unless quiet=true (returns 1 instead).
#
#   validate_json_file "auto_triage/data/commit_info.json"
#   validate_json_file "$f" "quiet"   # returns 1 instead of dying
#
validate_json_file() {
    local file="$1" quiet="${2:-}"

    if [ ! -f "$file" ]; then
        if [ "$quiet" = "quiet" ]; then return 1; else die "JSON file not found: $file"; fi
    fi
    if [ ! -s "$file" ]; then
        if [ "$quiet" = "quiet" ]; then return 1; else die "JSON file is empty: $file"; fi
    fi
    if ! jq empty "$file" 2>/dev/null; then
        if [ "$quiet" = "quiet" ]; then return 1; else die "Invalid JSON in $file"; fi
    fi
    return 0
}

# ==============================================================================
# Path / directory validation
# ==============================================================================

# ensure_dirs(dir, ...) -> 0
# Creates one or more directories if they do not exist.
#
#   ensure_dirs "$DATA_DIR" "$LOGS_DIR"
#
ensure_dirs() {
    local dir
    for dir in "$@"; do
        mkdir -p "$dir"
    done
}

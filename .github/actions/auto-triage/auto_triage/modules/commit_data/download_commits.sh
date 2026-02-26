#!/bin/bash
#
# download_commits.sh - Orchestrate downloading commit metadata between two commits
#
# Provides download_commits_between(start_commit, end_commit, output_file).
# Uses batch_downloader for actual downloads.
# Exit codes: 0 = success, 1 = error, 2 = caller must run batches.
# When returning 2, BATCH_COUNT is set as a variable (not added to exit code).
#
# Usage: source this file, then call download_commits_between
#

if [ -n "${_DOWNLOAD_COMMITS_LOADED:-}" ]; then
    return 0
fi
_DOWNLOAD_COMMITS_LOADED=1

_DC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=batch_downloader.sh
source "$_DC_DIR/batch_downloader.sh"

# Orchestrate commit metadata download between start and end.
# - If commit count <= BATCH_SIZE: downloads in one batch, returns 0
# - If count > BATCH_SIZE and <= MAX_BATCHES: sets BATCH_COUNT, returns 2 (caller runs batches)
# - If count > MAX_BATCHES: returns 1
#
#   download_commits_between "abc123" "def456" "auto_triage/data/commit_info.json"
#
download_commits_between() {
    local start_commit="$1"
    local end_commit="$2"
    local output_file="${3:-auto_triage/data/commit_info.json}"

    if [ -z "$start_commit" ] || [ -z "$end_commit" ]; then
        echo "download_commits: start and end commit required" >&2
        return 1
    fi

    if ! git rev-parse --verify "$start_commit" >/dev/null 2>&1; then
        echo "download_commits: start commit '$start_commit' not found" >&2
        return 1
    fi

    if ! git rev-parse --verify "$end_commit" >/dev/null 2>&1; then
        echo "download_commits: end commit '$end_commit' not found" >&2
        return 1
    fi

    local commits commit_count
    commits=$(git log --format="%H" --first-parent "$start_commit".."$end_commit")
    echo "$commits" | grep -q "^$end_commit$" || commits="$commits"$'\n'"$end_commit"
    commits=$(echo "$commits" | sort -u)
    commit_count=$(echo "$commits" | grep -c . 2>/dev/null || echo "0")

    if [ "$commit_count" -eq 0 ]; then
        mkdir -p "$(dirname "$output_file")"
        echo "[]" > "$output_file"
        return 0
    fi

    if [ "$commit_count" -gt "${AT_MAX_BATCHES:-100}" ]; then
        echo "download_commits: too many commits ($commit_count)" >&2
        return 1
    fi

    local batch_size="${AT_BATCH_SIZE:-10}"
    mkdir -p "$(dirname "$output_file")"
    rm -f "$output_file"
    echo "[]" > "$output_file"

    if [ "$commit_count" -le "$batch_size" ]; then
        download_commit_batch "$start_commit" "$end_commit" 0 "$output_file"
        return 0
    fi

    BATCH_COUNT=$(( (commit_count + batch_size - 1) / batch_size ))
    return 2
}

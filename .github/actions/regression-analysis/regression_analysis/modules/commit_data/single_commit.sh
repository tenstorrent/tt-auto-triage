#!/bin/bash
#
# single_commit.sh - Download metadata for a single commit
#
# Provides download_single_commit(commit_sha, output_file).
# Reuses batch_downloader logic (batch of 1).
#
# Usage: source this file, then call download_single_commit
#

if [ -n "${_SINGLE_COMMIT_LOADED:-}" ]; then
    return 0
fi
_SINGLE_COMMIT_LOADED=1

_SC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=batch_downloader.sh
source "$_SC_DIR/batch_downloader.sh"

# Download commit metadata for a single commit.
# Appends one entry to output_file (same schema as batch_downloader).
#
#   download_single_commit "abc123" "regression_analysis/data/commit_info.json"
#
download_single_commit() {
    local commit_sha="$1"
    local output_file="${2:-regression_analysis/data/commit_info.json}"

    if [ -z "$commit_sha" ]; then
        echo "single_commit: commit_sha is required" >&2
        return 1
    fi

    # Reuse batch_downloader: single commit = batch of 1 (start=end, batch_idx=0)
    download_commit_batch "$commit_sha" "$commit_sha" 0 "$output_file"
}

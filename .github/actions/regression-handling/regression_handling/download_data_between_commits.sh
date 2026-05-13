#!/bin/bash
#
# Controller script to orchestrate downloading Copilot metadata between two commits.
# Usage: ./download_data_between_commits.sh <start_commit> <end_commit> [output_file]
# - If commit span <= 10, downloads directly.
# - If commit span is between 11 and 100, instructs caller to run the batch script in chunks.
# - If commit span > 100, fails immediately.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=modules/commit_data/download_commits.sh
source "$SCRIPT_DIR/modules/commit_data/download_commits.sh"

if [ $# -lt 2 ]; then
    log_error "Missing required arguments"
    echo "Usage: $0 <start_commit> <end_commit> [output_file]"
    exit 1
fi

START_COMMIT="$1"
END_COMMIT="$2"
OUTPUT_FILE="${3:-regression_handling/data/commit_info.json}"

if ! git rev-parse --verify "$START_COMMIT" >/dev/null 2>&1; then
    log_error "Start commit '$START_COMMIT' not found"
    exit 1
fi
if ! git rev-parse --verify "$END_COMMIT" >/dev/null 2>&1; then
    log_error "End commit '$END_COMMIT' not found"
    exit 1
fi

log_success "Analyzing commits between"
echo "  Start: $START_COMMIT"
echo "  End:   $END_COMMIT"
echo ""

COMMITS=$(git log --format="%H" --first-parent "$START_COMMIT".."$END_COMMIT")
echo "$COMMITS" | grep -q "^$END_COMMIT$" || COMMITS="$COMMITS"$'\n'"$END_COMMIT"
COMMITS=$(echo "$COMMITS" | sort -u)
COMMIT_COUNT=$(echo "$COMMITS" | grep -c . 2>/dev/null || echo "0")

echo "Commits in range: $COMMIT_COUNT"

if [ "$COMMIT_COUNT" -eq 0 ]; then
    log_warn "No commits found between the provided SHAs."
fi

if download_commits_between "$START_COMMIT" "$END_COMMIT" "$OUTPUT_FILE"; then
    ret=0
else
    ret=$?
fi

if [ $ret -eq 0 ]; then
    exit 0
fi

if [ $ret -eq 1 ]; then
    log_error "Download failed: commit span exceeds limit (max ${AT_MAX_BATCHES:-100} commits) or another error occurred."
    exit 1
fi

# ret=2: need batches
BATCH_SIZE="${AT_BATCH_SIZE:-10}"
log_warn "Commit window requires ${BATCH_COUNT:-?} batches (limit per call: $BATCH_SIZE)."
echo "Run ./download_data_between_commits_batch.sh with indices 0 through $((${BATCH_COUNT:-0} - 1)) to build the full dataset."
echo "Use the same output file ('$OUTPUT_FILE') for each batch; results will be appended."
echo "BATCH_COUNT=${BATCH_COUNT:-0}"
exit 2

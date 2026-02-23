#!/bin/bash
#
# Download Copilot PR overview data for a specific batch of commits.
# Usage: ./download_data_between_commits_batch.sh <start_commit> <end_commit> <batch_index> [output_file]
# Each batch processes up to AT_BATCH_SIZE (default 10) commits. Batches are zero-indexed.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=modules/commit_data/batch_downloader.sh
source "$SCRIPT_DIR/modules/commit_data/batch_downloader.sh"

if [ $# -lt 3 ]; then
    log_error "Missing required arguments"
    echo "Usage: $0 <start_commit> <end_commit> <batch_index> [output_file]"
    echo "Example: $0 90336ff5cbacf818e3a20544e5f66b2088757e75 a253cee23e5362d6aba14b716b97f9fe302d6adc 0"
    exit 1
fi

START_COMMIT="$1"
END_COMMIT="$2"
BATCH_INDEX="$3"
OUTPUT_FILE="${4:-auto_triage/data/commit_info.json}"

if ! [[ "$BATCH_INDEX" =~ ^[0-9]+$ ]]; then
    log_error "batch_index must be a non-negative integer"
    exit 1
fi

log_success "Processing batch $BATCH_INDEX of commits between"
echo "  Start: $START_COMMIT"
echo "  End:   $END_COMMIT"
echo ""

download_commit_batch "$START_COMMIT" "$END_COMMIT" "$BATCH_INDEX" "$OUTPUT_FILE"

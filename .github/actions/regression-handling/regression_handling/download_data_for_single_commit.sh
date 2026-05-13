#!/bin/bash
#
# Utility script: download metadata for a single commit using the same
# schema as download_data_between_commits_batch.sh.
#
# Usage:
#   ./download_data_for_single_commit.sh <commit_sha> [output_file]
#
# If output_file is omitted, it defaults to regression_handling/data/commit_info.json
# and the entry is appended to the JSON array (creating it as [] if needed).
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=modules/commit_data/single_commit.sh
source "$SCRIPT_DIR/modules/commit_data/single_commit.sh"

if [ $# -lt 1 ]; then
    log_error "Missing required arguments"
    echo "Usage: $0 <commit_sha> [output_file]" >&2
    exit 1
fi

COMMIT_SHA="$1"
OUTPUT_FILE="${2:-regression_handling/data/commit_info.json}"

if ! git rev-parse --verify "$COMMIT_SHA" >/dev/null 2>&1; then
    log_error "commit '$COMMIT_SHA' not found"
    exit 1
fi

OUTPUT_DIR="$(dirname "$OUTPUT_FILE")"
mkdir -p "$OUTPUT_DIR"

# Ensure the output file exists and is a JSON array before appending.
if [ ! -f "$OUTPUT_FILE" ]; then
    echo "[]" > "$OUTPUT_FILE"
fi

log_success "Downloading metadata for single commit ${COMMIT_SHA}"
download_single_commit "$COMMIT_SHA" "$OUTPUT_FILE"

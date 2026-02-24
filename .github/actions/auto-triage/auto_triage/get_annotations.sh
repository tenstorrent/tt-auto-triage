#!/bin/bash
#
# Fetch check-run annotations for a given job URL.
# Usage: ./get_annotations.sh <job_url> [output_file]
#
# Writes failure/error annotations to output_file (default: auto_triage/logs/job_<id>/annotations.json).
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=modules/logs/log_parser.sh
source "$SCRIPT_DIR/modules/logs/log_parser.sh"

if [ $# -lt 1 ]; then
    log_error "Missing job URL"
    echo "Usage: $0 <job_url> [output_file]"
    exit 1
fi

JOB_URL="$1"
if ! parse_job_url "$JOB_URL"; then
    log_error "Unable to parse job URL: $JOB_URL"
    exit 1
fi

export AT_OWNER="$_owner"
export AT_REPO="$_repo"
export AT_OWNER_REPO="${_owner}/${_repo}"
JOB_ID="$_job_id"

# shellcheck source=lib/github_api.sh
source "$SCRIPT_DIR/lib/github_api.sh"

check_command gh jq

OUTPUT_FILE="${2:-auto_triage/logs/job_${JOB_ID}/annotations.json}"
OUTPUT_DIR=$(dirname "$OUTPUT_FILE")
mkdir -p "$OUTPUT_DIR"

log_info "Fetching annotations for job ${JOB_ID}"

JOB_INFO=$(get_job_info "$JOB_ID")
CHECK_RUN_URL=$(echo "$JOB_INFO" | jq -r '.check_run_url // empty' 2>/dev/null || echo "")

if [ -z "$CHECK_RUN_URL" ]; then
    log_warn "No check-run URL found for job ${JOB_ID}."
    echo '[]' > "$OUTPUT_FILE"
    exit 0
fi

CHECK_ID=$(echo "$CHECK_RUN_URL" | sed -n 's#.*/check-runs/\([0-9][0-9]*\).*#\1#p')
if [ -z "$CHECK_ID" ]; then
    log_warn "Could not parse check-run ID from URL."
    echo '[]' > "$OUTPUT_FILE"
    exit 0
fi

ALL_ANNOTS=$(get_check_annotations "$CHECK_ID")
FILTERED=$(echo "$ALL_ANNOTS" | jq '[.[] | select(((.annotation_level // "") | ascii_downcase) == "failure" or ((.annotation_level // "") | ascii_downcase) == "error")]' 2>/dev/null || echo '[]')

echo "$FILTERED" | jq '.' > "$OUTPUT_FILE"
TOTAL=$(echo "$FILTERED" | jq 'length' 2>/dev/null || echo 0)

if [ "$TOTAL" -eq 0 ]; then
    log_warn "No annotations returned for job ${JOB_ID}."
else
    log_success "Saved ${TOTAL} annotation(s) to ${OUTPUT_FILE}."
fi

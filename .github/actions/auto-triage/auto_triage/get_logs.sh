#!/bin/bash
#
# Download logs for a specific GitHub Actions job URL.
# Usage:
#   ./get_logs.sh <job_url> [output_directory]
#
# Example:
#   ./get_logs.sh \
#     https://github.com/tenstorrent/tt-metal/actions/runs/19475473285/job/55735804849
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=modules/logs/log_parser.sh
source "$SCRIPT_DIR/modules/logs/log_parser.sh"
# shellcheck source=lib/github_api.sh
source "$SCRIPT_DIR/lib/github_api.sh"

if [ $# -lt 1 ]; then
    log_error "Usage: $0 <job_url> [output_directory]"
    exit 1
fi

JOB_URL="$1"
OUTPUT_BASE="${2:-auto_triage/logs}"

check_command gh unzip

if ! parse_job_url "$JOB_URL"; then
    log_error "Unable to parse job URL. Expected format https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>"
    exit 1
fi

export AT_OWNER="$_owner"
export AT_REPO="$_repo"
export AT_OWNER_REPO="${_owner}/${_repo}"
OWNER="$_owner"
REPO="$_repo"
RUN_ID="$_run_id"
JOB_ID="$_job_id"

DEST_DIR="${OUTPUT_BASE%/}/job_${JOB_ID}"
rm -rf "$DEST_DIR"
mkdir -p "$DEST_DIR"

log_info "Fetching job metadata..."
JOB_INFO=$(get_job_info "$JOB_ID")
JOB_NAME=$(echo "$JOB_INFO" | jq -r '.name // ""')
JOB_ATTEMPT=$(echo "$JOB_INFO" | jq -r '.run_attempt // 1')

TMP_ZIP="$(mktemp --suffix=.zip 2>/dev/null || mktemp)"
TMP_UNZIP="$(mktemp -d)"
log_info "Downloading logs for run ${RUN_ID}..."
gh api "repos/${OWNER}/${REPO}/actions/runs/${RUN_ID}/logs" > "$TMP_ZIP"
unzip -oq "$TMP_ZIP" -d "$TMP_UNZIP"

log_info "Copying full log archive..."
FULL_DIR="${DEST_DIR}/full"
mkdir -p "$FULL_DIR"
cp -R "$TMP_UNZIP"/. "$FULL_DIR"/

MATCHED=()
while IFS= read -r rel; do
    [ -n "$rel" ] && MATCHED+=("$rel")
done < <(find_job_logs "$TMP_UNZIP" "$JOB_NAME")

if [ ${#MATCHED[@]} -eq 0 ]; then
    log_warn "Could not isolate job-specific logs; rely on 'full' directory."
else
    JOB_DIR="${DEST_DIR}/job_specific"
    for rel in "${MATCHED[@]}"; do
        src="$TMP_UNZIP/$rel"
        dest="$JOB_DIR/$rel"
        mkdir -p "$(dirname "$dest")"
        cp "$src" "$dest"
    done
    log_success "Extracted ${#MATCHED[@]} file(s) matching job name into ${JOB_DIR}"
fi

rm -f "$TMP_ZIP"
rm -rf "$TMP_UNZIP"

cat > "${DEST_DIR}/metadata.txt" <<EOF
Job URL: ${JOB_URL}
Repository: ${OWNER}/${REPO}
Run ID: ${RUN_ID}
Run Attempt: ${JOB_ATTEMPT}
Job ID: ${JOB_ID}
Job Name: ${JOB_NAME}
Downloaded: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

log_success "Logs available at: ${DEST_DIR}"

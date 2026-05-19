#!/bin/bash
#
# Download tt-triage artifacts for a failing job URL.
# Writes canonical files to: <output_directory>/hang_triage/
#   - triage_output.txt
#   - debug_bus_signal_groups.json
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=lib/validation.sh
source "$SCRIPT_DIR/lib/validation.sh"
# shellcheck source=lib/github_api.sh
source "$SCRIPT_DIR/lib/github_api.sh"

if [ $# -lt 1 ]; then
    log_error "Usage: $0 <job_url> [output_directory]"
    exit 1
fi

JOB_URL="$1"
OUTPUT_BASE="${2:-regression_analysis/data}"
DEST_DIR="${OUTPUT_BASE%/}/hang_triage"

check_command gh jq unzip

if ! parse_job_url "$JOB_URL"; then
    log_error "Invalid job URL: $JOB_URL"
    exit 1
fi

export AT_OWNER="$_owner"
export AT_REPO="$_repo"
export AT_OWNER_REPO="${_owner}/${_repo}"
RUN_ID="$_run_id"
JOB_ID="$_job_id"

rm -rf "$DEST_DIR"
mkdir -p "$DEST_DIR"

extract_check_run_id() {
    local job_id="$1"
    local check_url
    check_url="$(get_job_info "$job_id" | jq -r '.check_run_url // empty' 2>/dev/null || true)"
    echo "$check_url" | sed -n 's#.*/check-runs/\([0-9][0-9]*\).*#\1#p'
}

download_named_artifact() {
    local artifact_name="$1"
    local output_file="$2"
    local info zip_url tmp_zip tmp_dir src_file

    info="$(gh_api "repos/${AT_OWNER_REPO}/actions/runs/${RUN_ID}/artifacts?name=${artifact_name}" '{"artifacts":[]}' )"
    zip_url="$(echo "$info" | jq -r '.artifacts[0].archive_download_url // empty' 2>/dev/null || true)"
    if [ -z "$zip_url" ]; then
        log_warn "Artifact not found: ${artifact_name}"
        return 1
    fi

    tmp_zip="$(mktemp --suffix=.zip 2>/dev/null || mktemp)"
    tmp_dir="$(mktemp -d)"
    if ! gh api "$zip_url" >"$tmp_zip" 2>/dev/null; then
        log_warn "Failed to download artifact: ${artifact_name}"
        rm -f "$tmp_zip"; rm -rf "$tmp_dir"
        return 1
    fi
    if ! unzip -oq "$tmp_zip" -d "$tmp_dir" 2>/dev/null; then
        log_warn "Failed to unzip artifact: ${artifact_name}"
        rm -f "$tmp_zip"; rm -rf "$tmp_dir"
        return 1
    fi

    src_file="$(find "$tmp_dir" -type f -print 2>/dev/null | LC_ALL=C sort | jq -R -s 'split("\n") | map(select(length>0)) | .[0] // empty' -r)"
    if [ -z "$src_file" ]; then
        log_warn "Artifact zip empty: ${artifact_name}"
        rm -f "$tmp_zip"; rm -rf "$tmp_dir"
        return 1
    fi

    cp "$src_file" "${DEST_DIR}/${output_file}"
    rm -f "$tmp_zip"; rm -rf "$tmp_dir"
    log_success "Downloaded ${artifact_name} -> ${output_file}"
    return 0
}

CHECK_RUN_ID="$(extract_check_run_id "$JOB_ID")"
ARTIFACT_SUFFIX="${CHECK_RUN_ID:-$JOB_ID}"
[ -z "$CHECK_RUN_ID" ] && log_warn "Could not resolve check_run_id; using job_id suffix (${JOB_ID})."

TRIAGE_ARTIFACT="triage_output_${ARTIFACT_SUFFIX}"
DEBUG_ARTIFACT="debug_bus_signals_${ARTIFACT_SUFFIX}"
log_info "Target artifact names: ${TRIAGE_ARTIFACT}, ${DEBUG_ARTIFACT}"

DOWNLOADED=0
download_named_artifact "$TRIAGE_ARTIFACT" "triage_output.txt" && DOWNLOADED=$((DOWNLOADED + 1))
download_named_artifact "$DEBUG_ARTIFACT" "debug_bus_signal_groups.json" && DOWNLOADED=$((DOWNLOADED + 1))

if [ "$DOWNLOADED" -gt 0 ]; then
    log_success "Saved ${DOWNLOADED} hang artifact(s) to ${DEST_DIR}/"
else
    log_warn "No hang artifacts downloaded."
fi

cat >"${DEST_DIR}/metadata.txt" <<EOF
Job URL: ${JOB_URL}
Repository: ${AT_OWNER_REPO}
Run ID: ${RUN_ID}
Job ID: ${JOB_ID}
Check run ID: ${CHECK_RUN_ID:-}
Artifact suffix used: ${ARTIFACT_SUFFIX}
Artifacts downloaded: ${DOWNLOADED}
Downloaded: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

#!/bin/bash
#
# Download tt-triage artifacts (triage output and debug bus signals) for a
# GitHub Actions job that experienced a card hang.
#
# Usage:
#   ./get_triage_artifacts.sh <job_url> [output_directory]
#
# Example:
#   ./get_triage_artifacts.sh \
#     https://github.com/tenstorrent/tt-metal/actions/runs/23559518732/job/65883041234
#
# Artifacts are saved to <output_directory>/hang_triage/ (default: auto_triage/data/hang_triage/).
# The script looks for artifacts named:
#   - triage_output_<job_id>     (full tt-triage stdout+stderr capture)
#   - debug_bus_signals_<job_id> (structured JSON debug bus signal groups)
#
# If no matching artifacts are found the script exits 0 with a warning — the
# hang may not have produced artifacts (e.g., tt-triage crashed before writing).
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
OUTPUT_BASE="${2:-auto_triage/data}"

check_command gh jq unzip

if ! parse_job_url "$JOB_URL"; then
    log_error "Unable to parse job URL. Expected: https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>"
    exit 1
fi

export AT_OWNER="$_owner"
export AT_REPO="$_repo"
export AT_OWNER_REPO="${_owner}/${_repo}"
RUN_ID="$_run_id"
JOB_ID="$_job_id"

DEST_DIR="${OUTPUT_BASE%/}/hang_triage"
rm -rf "$DEST_DIR"
mkdir -p "$DEST_DIR"

log_info "Listing artifacts for run ${RUN_ID}..."
ARTIFACTS_JSON=$(gh_api "repos/${AT_OWNER_REPO}/actions/runs/${RUN_ID}/artifacts" '{"artifacts":[]}')
TOTAL_ARTIFACTS=$(echo "$ARTIFACTS_JSON" | jq '.total_count // 0')
log_info "Found ${TOTAL_ARTIFACTS} artifact(s) in run"

DOWNLOADED=0

download_artifact() {
    local artifact_id="$1"
    local artifact_name="$2"
    local dest_file="$3"

    local tmp_zip
    tmp_zip="$(mktemp --suffix=.zip 2>/dev/null || mktemp)"
    local tmp_dir
    tmp_dir="$(mktemp -d)"

    if gh api "repos/${AT_OWNER_REPO}/actions/artifacts/${artifact_id}/zip" > "$tmp_zip" 2>/dev/null; then
        if unzip -oq "$tmp_zip" -d "$tmp_dir" 2>/dev/null; then
            # Artifacts may contain a single file or a directory; flatten into dest
            local found_files=0
            while IFS= read -r f; do
                [ -f "$f" ] || continue
                local basename
                basename="$(basename "$f")"
                cp "$f" "${DEST_DIR}/${basename}"
                found_files=$((found_files + 1))
            done < <(find "$tmp_dir" -type f -print 2>/dev/null)

            if [ "$found_files" -gt 0 ]; then
                log_success "Downloaded artifact '${artifact_name}' (${found_files} file(s))"
                DOWNLOADED=$((DOWNLOADED + 1))
            else
                log_warn "Artifact '${artifact_name}' zip was empty"
            fi
        else
            log_warn "Failed to unzip artifact '${artifact_name}'"
        fi
    else
        log_warn "Failed to download artifact '${artifact_name}'"
    fi

    rm -f "$tmp_zip"
    rm -rf "$tmp_dir"
}

# Search for triage_output artifact (named triage_output_<check_run_id>)
TRIAGE_OUTPUT_ID=$(echo "$ARTIFACTS_JSON" | jq -r \
    --arg job_id "$JOB_ID" \
    '.artifacts[] | select(.name == ("triage_output_" + $job_id)) | .id // empty' 2>/dev/null | head -1)

if [ -z "$TRIAGE_OUTPUT_ID" ]; then
    # Fallback: search by prefix in case naming convention differs
    TRIAGE_OUTPUT_ID=$(echo "$ARTIFACTS_JSON" | jq -r \
        '.artifacts[] | select(.name | startswith("triage_output_")) | .id // empty' 2>/dev/null | head -1)
fi

if [ -n "$TRIAGE_OUTPUT_ID" ]; then
    TRIAGE_NAME=$(echo "$ARTIFACTS_JSON" | jq -r \
        --arg id "$TRIAGE_OUTPUT_ID" \
        '.artifacts[] | select(.id == ($id | tonumber)) | .name' 2>/dev/null)
    download_artifact "$TRIAGE_OUTPUT_ID" "$TRIAGE_NAME" "triage_output.txt"
else
    log_warn "No triage_output artifact found for run ${RUN_ID}"
fi

# Search for debug_bus_signals artifact (named debug_bus_signals_<check_run_id>)
DEBUG_SIGNALS_ID=$(echo "$ARTIFACTS_JSON" | jq -r \
    --arg job_id "$JOB_ID" \
    '.artifacts[] | select(.name == ("debug_bus_signals_" + $job_id)) | .id // empty' 2>/dev/null | head -1)

if [ -z "$DEBUG_SIGNALS_ID" ]; then
    # Fallback: search by prefix (older naming used a UUID instead of job_id)
    DEBUG_SIGNALS_ID=$(echo "$ARTIFACTS_JSON" | jq -r \
        '.artifacts[] | select(.name | startswith("debug_bus_signals_")) | .id // empty' 2>/dev/null | head -1)
fi

if [ -n "$DEBUG_SIGNALS_ID" ]; then
    DEBUG_NAME=$(echo "$ARTIFACTS_JSON" | jq -r \
        --arg id "$DEBUG_SIGNALS_ID" \
        '.artifacts[] | select(.id == ($id | tonumber)) | .name' 2>/dev/null)
    download_artifact "$DEBUG_SIGNALS_ID" "$DEBUG_NAME" "debug_bus_signal_groups.json"
else
    log_warn "No debug_bus_signals artifact found for run ${RUN_ID}"
fi

if [ "$DOWNLOADED" -gt 0 ]; then
    log_success "Hang triage artifacts saved to ${DEST_DIR}/"
    ls -la "$DEST_DIR/"
else
    log_warn "No hang triage artifacts were downloaded. The hang may not have produced downloadable artifacts."
fi

cat > "${DEST_DIR}/metadata.txt" <<EOF
Job URL: ${JOB_URL}
Repository: ${AT_OWNER_REPO}
Run ID: ${RUN_ID}
Job ID: ${JOB_ID}
Artifacts downloaded: ${DOWNLOADED}
Downloaded: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

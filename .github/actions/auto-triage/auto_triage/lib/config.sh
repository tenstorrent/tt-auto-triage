#!/bin/bash
#
# config.sh - Centralised configuration for auto-triage scripts
#
# All values are overridable via environment variables so that tests and
# alternative deployments can customise behaviour without editing code.
#
# Usage: source this file (it pulls in common.sh automatically).
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/config.sh"
#

# Guard against double-sourcing
if [ -n "${_AUTO_TRIAGE_CONFIG_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_CONFIG_LOADED=1

# config.sh depends on common.sh (AUTO_TRIAGE_ROOT, get_data_dir, etc.)
_CONFIG_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$_CONFIG_LIB_DIR/common.sh"

# ==============================================================================
# Repository  (the repo being triaged, NOT the auto-triage repo itself)
# ==============================================================================
AT_OWNER="${AT_OWNER:-tenstorrent}"
AT_REPO="${AT_REPO:-tt-metal}"
AT_OWNER_REPO="${AT_OWNER_REPO:-${AT_OWNER}/${AT_REPO}}"
AT_BASE_URL="${AT_BASE_URL:-https://github.com/${AT_OWNER_REPO}}"

# ==============================================================================
# Numeric constants  (used by more than one script)
# ==============================================================================
AT_BATCH_SIZE="${AT_BATCH_SIZE:-10}"            # commits per download batch
AT_MAX_BATCHES="${AT_MAX_BATCHES:-100}"         # max commit range before failing
AT_PER_PAGE="${AT_PER_PAGE:-100}"               # GitHub API results per page
AT_FAILURE_LIMIT="${AT_FAILURE_LIMIT:-30}"      # consecutive failures before stopping boundary search

# ==============================================================================
# Feature flags  (mirror the action.yml inputs where relevant)
# ==============================================================================
AT_CUTOFF_COMMIT="${AT_CUTOFF_COMMIT:-${CUTOFF_COMMIT:-}}"           # ignore runs newer than this SHA
AT_REUSE_DATA="${AT_REUSE_DATA:-${REUSE_DATA:-false}}"            # skip re-downloading data if present

# ==============================================================================
# Directory helpers  (thin wrappers around common.sh, adding mkdir + symlinks)
# ==============================================================================

# Create canonical data/logs/output directories and their convenience symlinks.
# Call once during script initialisation (filter_triage.sh, auto_triage.sh, etc.)
#
#   setup_triage_dirs [root]
#
# Sets globals: CANON_DATA_DIR, CANON_LOGS_DIR, CANON_OUTPUT_DIR
setup_triage_dirs() {
    local root="${1:-$AUTO_TRIAGE_ROOT}"

    CANON_DATA_DIR="$(get_data_dir "$root")"
    CANON_LOGS_DIR="$(get_logs_dir "$root")"
    CANON_OUTPUT_DIR="$(get_output_dir "$root")"

    mkdir -p "$CANON_DATA_DIR" "$CANON_LOGS_DIR" "$CANON_OUTPUT_DIR"

    # Convenience symlinks (./data -> auto_triage/data, etc.)
    ln -sfn auto_triage/data   "${root}/data"
    ln -sfn auto_triage/logs   "${root}/logs"
    ln -sfn auto_triage/output "${root}/output"
}

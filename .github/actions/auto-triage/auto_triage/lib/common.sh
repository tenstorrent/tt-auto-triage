#!/bin/bash
#
# common.sh - Shared utilities for auto-triage scripts
#
# Provides:
# - Color output functions (log_info, log_error, log_warn, log_success)
# - Path resolution functions (get_data_dir, get_output_dir, get_logs_dir)
# - Error handling helpers (die, warn, check_command)
# - JSON manipulation helpers (jq_safe, json_get)
# - Environment variable helpers (require_env, get_env_with_default)
#
# Usage: source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
# Or from a script in auto_triage/: source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
#

# Prevent double-sourcing
if [ -n "${AUTO_TRIAGE_COMMON_LOADED:-}" ]; then
    return 0
fi
AUTO_TRIAGE_COMMON_LOADED=1

# ------------------------------------------------------------------------------
# Color constants (only if stdout is a TTY, otherwise unset for piping/logs)
# ------------------------------------------------------------------------------
if [ -t 1 ]; then
    AUTO_TRIAGE_RED='\033[0;31m'
    AUTO_TRIAGE_GREEN='\033[0;32m'
    AUTO_TRIAGE_YELLOW='\033[1;33m'
    AUTO_TRIAGE_BLUE='\033[0;34m'
    AUTO_TRIAGE_NC='\033[0m'
else
    AUTO_TRIAGE_RED=''
    AUTO_TRIAGE_GREEN=''
    AUTO_TRIAGE_YELLOW=''
    AUTO_TRIAGE_BLUE=''
    AUTO_TRIAGE_NC=''
fi

# ------------------------------------------------------------------------------
# Root path resolution - auto-detect when sourced from lib/common.sh
# ------------------------------------------------------------------------------
_AUTO_TRIAGE_LIB_DIR="${_AUTO_TRIAGE_LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd)}"
AUTO_TRIAGE_ROOT="${AUTO_TRIAGE_ROOT:-$(cd "${_AUTO_TRIAGE_LIB_DIR}/.." 2>/dev/null && pwd)}"

# ------------------------------------------------------------------------------
# Color output functions
# ------------------------------------------------------------------------------

# Log informational message (blue)
log_info() {
    echo -e "${AUTO_TRIAGE_BLUE}$*${AUTO_TRIAGE_NC}"
}

# Log error message (red) to stderr
log_error() {
    echo -e "${AUTO_TRIAGE_RED}$*${AUTO_TRIAGE_NC}" >&2
}

# Log warning message (yellow) to stderr
log_warn() {
    echo -e "${AUTO_TRIAGE_YELLOW}$*${AUTO_TRIAGE_NC}" >&2
}

# Log success message (green)
log_success() {
    echo -e "${AUTO_TRIAGE_GREEN}$*${AUTO_TRIAGE_NC}"
}

# ------------------------------------------------------------------------------
# Path resolution functions
# Returns canonical paths relative to AUTO_TRIAGE_ROOT
# ------------------------------------------------------------------------------

# Get the data directory path (auto_triage/data)
get_data_dir() {
    local root="${1:-$AUTO_TRIAGE_ROOT}"
    if [ -z "$root" ]; then
        echo "auto_triage/data"
        return
    fi
    echo "${root}/auto_triage/data"
}

# Get the output directory path (auto_triage/output)
get_output_dir() {
    local root="${1:-$AUTO_TRIAGE_ROOT}"
    if [ -z "$root" ]; then
        echo "auto_triage/output"
        return
    fi
    echo "${root}/auto_triage/output"
}

# Get the logs directory path (auto_triage/logs)
get_logs_dir() {
    local root="${1:-$AUTO_TRIAGE_ROOT}"
    if [ -z "$root" ]; then
        echo "auto_triage/logs"
        return
    fi
    echo "${root}/auto_triage/logs"
}

# ------------------------------------------------------------------------------
# Error handling helpers
# ------------------------------------------------------------------------------

# Exit immediately with error message
# Usage: die "Error message"
die() {
    log_error "Error: $*"
    exit 1
}

# Print warning but continue
# Usage: warn "Warning message"
warn() {
    log_warn "Warning: $*"
}

# Check that a command exists; die if not
# Usage: check_command jq gh
check_command() {
    local cmd
    for cmd in "$@"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            die "$cmd is required but not found in PATH."
        fi
    done
}

# ------------------------------------------------------------------------------
# JSON manipulation helpers
# ------------------------------------------------------------------------------

# Run jq with safe fallback for missing/invalid files
# Usage: result=$(jq_safe -r '.key' file.json) || default_value
# Returns exit 1 if jq fails, so use with: val=$(jq_safe ...) || val="default"
jq_safe() {
    if [ $# -lt 2 ]; then
        echo "Usage: jq_safe <jq_args> <file>" >&2
        return 1
    fi
    local file="${*: -1}"
    local jq_args=("${@:1:$#-1}")
    if [ ! -f "$file" ]; then
        return 1
    fi
    jq "${jq_args[@]}" "$file" 2>/dev/null || return 1
}

# Get JSON value with optional default
# Usage: val=$(json_get .key file.json "default")
# Usage: val=$(json_get .key file.json)
json_get() {
    local path="$1"
    local file="$2"
    local default="${3:-}"
    local result
    result=$(jq_safe -r "$path" "$file") || true
    if [ -n "$result" ] && [ "$result" != "null" ]; then
        echo "$result"
    else
        echo "$default"
    fi
}

# ------------------------------------------------------------------------------
# Environment variable helpers
# ------------------------------------------------------------------------------

# Require an environment variable; die if unset or empty
# Usage: require_env GITHUB_TOKEN
# Usage: require_env SLACK_BOT_TOKEN "Set SLACK_BOT_TOKEN to send notifications"
require_env() {
    local var_name="$1"
    local msg="${2:-$var_name must be set}"
    local val="${!var_name:-}"
    if [ -z "$val" ]; then
        die "$msg"
    fi
}

# Get env var with default value
# Usage: token=$(get_env_with_default COPILOT_PAT "$GH_TOKEN")
get_env_with_default() {
    local var_name="$1"
    local default="$2"
    local val="${!var_name:-}"
    if [ -n "$val" ]; then
        echo "$val"
    else
        echo "$default"
    fi
}

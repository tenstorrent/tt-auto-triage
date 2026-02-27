#!/bin/bash
#
# common.sh - Shared utilities for auto-triage scripts
#
# Provides:
#   Logging    - log_info, log_error, log_warn, log_success
#   Paths      - get_data_dir, get_output_dir, get_logs_dir
#   Errors     - die, warn, check_command
#   JSON       - jq_safe, json_get
#   Env vars   - get_env_with_default
#
# Usage (from any script under auto_triage/):
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
#

# Guard against double-sourcing
if [ -n "${_AUTO_TRIAGE_COMMON_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_COMMON_LOADED=1

# ==============================================================================
# Root path resolution
# ==============================================================================
# auto_triage/lib/common.sh  ->  lib dir is dirname, root is one level up.
_AUTO_TRIAGE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_TRIAGE_ROOT="${AUTO_TRIAGE_ROOT:-$(cd "$_AUTO_TRIAGE_LIB_DIR/.." && pwd)}"

# ==============================================================================
# Colors  (enabled when stdout is a TTY *or* inside CI, matching existing scripts)
# ==============================================================================
if [ -t 1 ] || [ "${CI:-}" = "true" ]; then
    _AT_RED='\033[0;31m'
    _AT_GREEN='\033[0;32m'
    _AT_YELLOW='\033[1;33m'
    _AT_BLUE='\033[0;34m'
    _AT_NC='\033[0m'
else
    _AT_RED=''
    _AT_GREEN=''
    _AT_YELLOW=''
    _AT_BLUE=''
    _AT_NC=''
fi

# ==============================================================================
# Logging
# ==============================================================================

# log_info(message) -> (prints to stdout)
# Prints informational message in blue.
log_info()    { printf '%b\n' "${_AT_BLUE}$*${_AT_NC}"; }        # informational (blue)
# log_success(message) -> (prints to stdout)
# Prints success message in green.
log_success() { printf '%b\n' "${_AT_GREEN}$*${_AT_NC}"; }       # success       (green)
# log_warn(message) -> (prints to stderr)
# Prints warning message in yellow to stderr.
log_warn()    { printf '%b\n' "${_AT_YELLOW}$*${_AT_NC}" >&2; }  # warning       (yellow, stderr)
# log_error(message) -> (prints to stderr)
# Prints error message in red to stderr.
log_error()   { printf '%b\n' "${_AT_RED}$*${_AT_NC}" >&2; }     # error         (red, stderr)

# ==============================================================================
# Error handling
# ==============================================================================

# die(message) -> (exits 1)
# Prints error message and exits with code 1.
die() { log_error "Error: $*"; exit 1; }

# warn(message) -> (prints to stderr)
# Prints warning message and continues execution.
warn() { log_warn "Warning: $*"; }

# Die if any of the listed commands are missing.
#   check_command jq gh copilot
check_command() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required but not found in PATH."
    done
}

# ==============================================================================
# Path helpers  (canonical paths relative to AUTO_TRIAGE_ROOT)
# ==============================================================================

# get_data_dir([root]) -> path
# Returns path to auto_triage/data directory.
get_data_dir()   { echo "${1:-$AUTO_TRIAGE_ROOT}/auto_triage/data"; }
# get_output_dir([root]) -> path
# Returns path to auto_triage/output directory.
get_output_dir() { echo "${1:-$AUTO_TRIAGE_ROOT}/auto_triage/output"; }
# get_logs_dir([root]) -> path
# Returns path to auto_triage/logs directory.
get_logs_dir()   { echo "${1:-$AUTO_TRIAGE_ROOT}/auto_triage/logs"; }

# ==============================================================================
# JSON helpers  (require jq at call-time, not at source-time)
# ==============================================================================

# Safe jq wrapper: returns 1 when the file is missing or jq fails.
#   result=$(jq_safe -r '.key' file.json) || result="default"
jq_safe() {
    [ $# -ge 2 ] || { echo "Usage: jq_safe <jq_args...> <file>" >&2; return 1; }
    command -v jq >/dev/null 2>&1 || return 1
    local file="${@: -1}"
    [ -f "$file" ] || return 1
    jq "${@:1:$#-1}" "$file" 2>/dev/null || return 1
}

# Convenience: extract a value or fall back to a default.
#   val=$(json_get .key file.json "fallback")
json_get() {
    [ $# -ge 2 ] || { echo "Usage: json_get <jq_path> <file> [default]" >&2; return 1; }
    local jq_path="$1" file="$2" default="${3:-}"
    local result
    result=$(jq_safe -r "$jq_path" "$file") || true
    if [ -n "$result" ] && [ "$result" != "null" ]; then
        echo "$result"
    else
        echo "$default"
    fi
}

# ==============================================================================
# Environment variable helpers
# ==============================================================================

# Return the value of an env var, or a default if unset/empty.
#   token=$(get_env_with_default COPILOT_PAT "$GH_TOKEN")
get_env_with_default() {
    [ $# -ge 2 ] || die "get_env_with_default: expected VAR_NAME and DEFAULT arguments"
    local var_name="$1" default_value="$2"
    if ! [[ "$var_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        die "get_env_with_default: invalid environment variable name: '$var_name'"
    fi
    local val="${!var_name:-}"
    echo "${val:-$default_value}"
}

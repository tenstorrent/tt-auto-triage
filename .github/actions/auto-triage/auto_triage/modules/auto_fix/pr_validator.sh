#!/bin/bash
#
# pr_validator.sh - Validation helpers for the auto-fix module
#
# Provides:
#   is_auto_fix_enabled(flag_file) -> 0 if create_PR is true, 1 otherwise
#   validate_explanation_file(path) -> 0 if file exists and is non-empty
#   validate_workspace(dir) -> 0 if directory contains a .git checkout
#
# Usage: source this file.
#

if [ -n "${_PR_VALIDATOR_LOADED:-}" ]; then
    return 0
fi
_PR_VALIDATOR_LOADED=1

_PV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "$_PV_DIR/../../lib/common.sh"

# Check whether auto-fix is enabled via the JSON flag file.
# Creates the flag file with create_PR=false if it doesn't exist.
is_auto_fix_enabled() {
    local flag_file="${1:-}"
    if [ -z "$flag_file" ]; then
        log_error "is_auto_fix_enabled: flag_file path required"
        return 1
    fi

    if [ ! -f "$flag_file" ]; then
        echo '{"create_PR": false}' > "$flag_file"
    fi

    local val
    val=$(jq -r '.create_PR // false' "$flag_file" 2>/dev/null || echo "false")
    [ "$val" = "true" ]
}

# Verify that the explanation file exists and is non-empty.
validate_explanation_file() {
    local path="${1:-}"
    if [ -z "$path" ]; then
        log_error "validate_explanation_file: path required"
        return 1
    fi
    [ -f "$path" ] && [ -s "$path" ]
}

# Verify that the workspace directory contains a git checkout.
validate_workspace() {
    local dir="${1:-}"
    if [ -z "$dir" ]; then
        log_error "validate_workspace: directory required"
        return 1
    fi
    [ -d "$dir/.git" ]
}

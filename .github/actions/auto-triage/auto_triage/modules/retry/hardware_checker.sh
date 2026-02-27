#!/bin/bash
#
# hardware_checker.sh - Hardware support checks for retry logic
#
# Provides:
#   is_hardware_supported(job_name) -> bool (0 = supported, 1 = not)
#   get_hardware_type(job_name)      -> n150|n300|p100a|p150|p300|unknown
#
# Supported: N150, N300, P100A, P150, P300 (case-insensitive)
# Excluded:  galaxy, T3K, T3000 (too expensive for automatic retries)
#
# Usage: source this file.
#

if [ -n "${_HARDWARE_CHECKER_LOADED:-}" ]; then
    return 0
fi
_HARDWARE_CHECKER_LOADED=1

# Source common for path resolution (optional; module may be used standalone)
_MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_MODULE_DIR}/../../lib"
# shellcheck source=../../lib/common.sh
[ -f "${_LIB_DIR}/common.sh" ] && source "${_LIB_DIR}/common.sh"

# ==============================================================================
# get_hardware_type(job_name) -> n150|n300|p100a|p150|p300|unknown
# ==============================================================================
get_hardware_type() {
    local job_name="${1:-}"
    local lower
    lower=$(echo "$job_name" | tr '[:upper:]' '[:lower:]')

    if [[ "$lower" == *"n150"* ]]; then
        echo "n150"
    elif [[ "$lower" == *"n300"* ]]; then
        echo "n300"
    elif [[ "$lower" == *"p100a"* ]] || [[ "$lower" == *"p100"* ]]; then
        echo "p100a"
    elif [[ "$lower" == *"p150"* ]]; then
        echo "p150"
    elif [[ "$lower" == *"p300"* ]]; then
        echo "p300"
    else
        echo "unknown"
    fi
}

# ==============================================================================
# is_hardware_supported(job_name) -> 0 if supported, 1 if not
# ==============================================================================
# Supported: N150, N300, P100A, P150, P300
# Excluded:  galaxy, T3K, T3000
is_hardware_supported() {
    local job_name="${1:-}"
    local lower
    lower=$(echo "$job_name" | tr '[:upper:]' '[:lower:]')

    # Must contain at least one supported hardware type
    if ! echo "$lower" | grep -qE '(n150|n300|p150|p300|p100)'; then
        return 1
    fi

    # Must NOT contain expensive hardware
    if echo "$lower" | grep -qE '(galaxy|t3k|t3000)'; then
        return 1
    fi

    return 0
}

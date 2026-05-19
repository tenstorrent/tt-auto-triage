#!/bin/bash
#
# log_parser.sh - Parse job URLs and find log files
#
# Provides: sanitize_job_name, find_job_logs.
# Uses lib/validation.sh for parse_job_url.
#
# Usage: source this file, then call the functions.
#

if [ -n "${_LOG_PARSER_LOADED:-}" ]; then
    return 0
fi
_LOG_PARSER_LOADED=1

_LP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/validation.sh
source "$_LP_DIR/../../lib/validation.sh"

# parse_job_url is provided by validation.sh; we re-export for convenience.
# Call: parse_job_url "$url" -> sets _owner, _repo, _run_id, _job_id

# sanitize_job_name(job_name) -> sanitized string to stdout
# Lowercases and strips to alphanumeric only for matching.
sanitize_job_name() {
    printf '%s\n' "$1" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:]'
    return 0
}

# find_job_logs(log_dir, job_name) -> matching relative paths to stdout (one per line)
# Finds files in log_dir whose sanitized path contains the sanitized job name.
find_job_logs() {
    local log_dir="$1"
    local job_name="$2"
    local job_key
    job_key=$(sanitize_job_name "$job_name")
    if [ -z "$job_key" ]; then
        return 0
    fi
    if [ ! -d "$log_dir" ]; then
        return 0
    fi
    local file
    while IFS= read -r file; do
        [ -f "$file" ] || continue
        local rel="${file#$log_dir}"
        rel="${rel#/}"
        if echo "$(sanitize_job_name "$rel")" | grep -q "$job_key"; then
            echo "$rel"
        fi
    done < <(find "$log_dir" -type f -print 2>/dev/null || true)
}

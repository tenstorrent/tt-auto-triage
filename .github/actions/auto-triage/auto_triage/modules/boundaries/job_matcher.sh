#!/bin/bash
#
# job_matcher.sh - Subjob name matching for workflow run processing
#
# Provides match_subjob() to determine if a GitHub Actions job name matches
# a requested subjob, with Unicode normalization and multiple matching strategies.
#
# Usage: source this file.
#

if [ -n "${_JOB_MATCHER_LOADED:-}" ]; then
    return 0
fi
_JOB_MATCHER_LOADED=1

# Normalize a string for matching: Unicode dashes → ASCII '-', lowercase.
# Uses same logic as find_boundaries.sh jq filter for consistency.
_normalize_job_name() {
    python3 - "$1" <<'PY'
import sys, unicodedata
text = sys.argv[1]
normalized = ''.join('-' if unicodedata.category(ch) == 'Pd' else ch for ch in text)
print(normalized.lower(), end='')
PY
}

# Check if a job name matches the requested subjob.
# Matching strategies: exact, "workflow / subjob", endswith, contains.
#
#   match_subjob "yolov5x-N150-func" "yolov5x-N150-func" "single-card-demo-tests"
#   match_subjob "single-card-demo-tests / yolov5x-N150-func" "yolov5x-N150-func" "single-card-demo-tests"
#
match_subjob() {
    local job_name="$1" subjob_name="$2" workflow_name="${3:-}"
    [ -n "$job_name" ] || return 1
    [ -n "$subjob_name" ] || return 1

    local n s w ws
    n=$(_normalize_job_name "$job_name")
    s=$(_normalize_job_name "$subjob_name")
    w=$(_normalize_job_name "$workflow_name")
    ws="${w} / ${s}"

    [ "$n" = "$s" ] && return 0
    [ -n "$w" ] && [ "$n" = "$ws" ] && return 0
    [[ "$n" == *"$s" ]] && return 0
    [[ "$n" == *"$s"* ]] && return 0
    return 1
}

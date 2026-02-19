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

# Normalize strings for matching: Unicode dashes → ASCII '-', lowercase.
# Character set must match find_boundaries.sh jq filter exactly:
# U+2010 U+2011 U+2012 U+2013 U+2014 U+2015 U+2212 U+FE58 U+FE63 U+FF0D.
# Single python3 invocation for all three strings to avoid process-per-call overhead.
# The python3 version used here is 3.12.3 (the default in Ubuntu 24.04)
match_subjob() {
    local job_name="${1-}" subjob_name="${2-}" workflow_name="${3-}"
    [ -n "$job_name" ] || return 1
    [ -n "$subjob_name" ] || return 1

    local n s w ws
    local _norm=()
    while IFS= read -r _line; do
        _norm+=("$_line")
    done < <(python3 - "${job_name}" "${subjob_name}" "${workflow_name}" <<'PY'
import sys
DASHES = frozenset('\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d')
def normalize(t): return ''.join('-' if c in DASHES else c for c in t).lower()
for i, v in enumerate(sys.argv[1:]):
    if i: sys.stdout.write('\n')
    sys.stdout.write(normalize(v))
sys.stdout.write('\n')
PY
)
    n="${_norm[0]:-}"
    s="${_norm[1]:-}"
    w="${_norm[2]:-}"
    ws="${w} / ${s}"

    [ "$n" = "$s" ] && return 0
    [ -n "$w" ] && [ "$n" = "$ws" ] && return 0
    [[ "$n" == *"$s" ]] && return 0
    [[ "$n" == *"$s"* ]] && return 0
    return 1
}

# Matching strategies: exact, "workflow / subjob", endswith, contains.
#   match_subjob "yolov5x-N150-func" "yolov5x-N150-func" "single-card-demo-tests"
#   match_subjob "single-card-demo-tests / yolov5x-N150-func" "yolov5x-N150-func" "single-card-demo-tests"

#!/usr/bin/env bash
#
# common.sh — Bug-escape shared helpers.
#
# Sources the upstream auto-triage libs (common, config, github_api) and
# adds a few bug-escape-specific utilities on top.

if [ -n "${_BUG_ESCAPES_COMMON_LOADED:-}" ]; then
  return 0
fi
_BUG_ESCAPES_COMMON_LOADED=1

_BE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUG_ESCAPES_ROOT="$(cd "$_BE_LIB_DIR/.." && pwd)"

# Upstream auto-triage libs (common → config → github_api)
_AT_LIB_DIR="$BUG_ESCAPES_ROOT/../.github/actions/auto-triage/auto_triage/lib"
if [ -d "$_AT_LIB_DIR" ]; then
  # shellcheck source=/dev/null
  source "$_AT_LIB_DIR/github_api.sh"
else
  # Fallback: define minimal stubs so the script can still be sourced
  echo "Warning: auto-triage libs not found at $_AT_LIB_DIR" >&2
  log_info()    { printf '[INFO] %s\n' "$*"; }
  log_success() { printf '[OK]   %s\n' "$*"; }
  log_warn()    { printf '[WARN] %s\n' "$*" >&2; }
  log_error()   { printf '[ERR]  %s\n' "$*" >&2; }
  die()         { log_error "Error: $*"; exit 1; }
  check_command() {
    for cmd in "$@"; do
      command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required but not found in PATH."
    done
  }
fi

# Source the cursor agent wrapper
# shellcheck source=cursor_agent.sh
source "$_BE_LIB_DIR/cursor_agent.sh"

# -------------------------------------------------------------------
# Bug-escape-specific helpers
# -------------------------------------------------------------------

# Read layer-mapping.json values.  Caches the parsed file in a variable.
_BE_LAYER_MAPPING=""
be_layer_mapping() {
  if [ -z "$_BE_LAYER_MAPPING" ]; then
    _BE_LAYER_MAPPING=$(cat "$BUG_ESCAPES_ROOT/config/layer-mapping.json")
  fi
  echo "$_BE_LAYER_MAPPING"
}

# Map a file path to an architectural layer using directory prefix matching.
# Returns the layer name, or "unknown" if no prefix matches.
#   layer=$(be_file_to_layer "tt_metal/impl/dispatch/foo.cpp")
be_file_to_layer() {
  local filepath="$1"
  local mapping
  mapping=$(be_layer_mapping)
  local layer
  layer=$(echo "$mapping" | jq -r --arg fp "$filepath" '
    .directory_prefixes
    | to_entries
    | map(. as $e | select($fp | startswith($e.key)))
    | sort_by(-(.key | length))
    | .[0].value // "unknown"
  ')
  echo "$layer"
}

# Determine the dominant layer for a set of changed files (JSON array of paths).
# Returns the most frequently occurring layer among the files.
#   layer=$(be_dominant_layer '["tt_metal/impl/foo.cpp","ttnn/bar.py"]')
be_dominant_layer() {
  local files_json="$1"
  local mapping
  mapping=$(be_layer_mapping)

  echo "$files_json" | jq -r --argjson m "$mapping" '
    [.[] | strings | . as $fp |
      ($m.directory_prefixes | to_entries
       | map(. as $e | select($fp | startswith($e.key)))
       | sort_by(-(.key | length))
       | .[0].value // "unknown")
    ]
    | group_by(.) | sort_by(-length) | .[0][0] // "unknown"
  '
}

# Compute a stable failure fingerprint from a log excerpt or error line.
# Strips timestamps, line numbers, memory addresses, PIDs, and UUIDs to
# produce a signature that stays the same across runs for the same root cause.
#   fp=$(compute_failure_fingerprint "AssertionError: Device has 2 program cache entries")
compute_failure_fingerprint() {
  local raw="$1"
  echo "$raw" \
    | sed -E 's/0x[0-9a-fA-F]+/0xADDR/g' \
    | sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}[.0-9Z]*/TIMESTAMP/g' \
    | sed -E 's/pid: [0-9]+/pid: PID/g' \
    | sed -E 's/tid: [0-9]+/tid: TID/g' \
    | sed -E 's/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/UUID/g' \
    | sed -E 's/:[0-9]+\)/:\)/g' \
    | tr -s ' ' \
    | head -1
}

# Cached workflow ID lookup — avoids repeat API calls for the same workflow
# across Phase 2 and Phase 3.
_WF_ID_CACHE_DIR=""
cached_get_workflow_id() {
  local wf_basename="$1"
  if [ -z "$_WF_ID_CACHE_DIR" ]; then
    _WF_ID_CACHE_DIR="$(mktemp -d)"
  fi
  local cache_file="$_WF_ID_CACHE_DIR/${wf_basename}.id"
  if [ -f "$cache_file" ]; then
    cat "$cache_file"
    return 0
  fi
  local wf_id
  wf_id=$(get_workflow_id "$wf_basename" 2>/dev/null || echo "")
  if [ -n "$wf_id" ]; then
    echo "$wf_id" > "$cache_file"
  fi
  echo "$wf_id"
}

# Compare two layers and return the escape type.
#   escape_type=$(be_classify_escape "ttnn" "tt-metalium")
#   -> "vertical" (fix in lower layer than test)
be_classify_escape() {
  local test_layer="$1" fix_layer="$2"
  local mapping
  mapping=$(be_layer_mapping)

  local test_level fix_level
  test_level=$(echo "$mapping" | jq -r --arg l "$test_layer" '.layer_hierarchy[$l] // -1')
  fix_level=$(echo "$mapping" | jq -r --arg l "$fix_layer" '.layer_hierarchy[$l] // -1')

  if [ "$test_level" = "-1" ] || [ "$fix_level" = "-1" ]; then
    echo "unknown"
  elif [ "$fix_level" -lt "$test_level" ]; then
    echo "vertical"
  elif [ "$fix_level" -eq "$test_level" ]; then
    echo "horizontal"
  else
    echo "unknown"
  fi
}

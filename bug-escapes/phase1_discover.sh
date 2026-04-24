#!/usr/bin/env bash
set -euo pipefail

# Phase 1: Discovery & Mapping
#
# Reads the static workflow-layers.json config and writes pipeline-config.json.
# No agent calls — layer classifications are maintained by hand in config/.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
PIPELINE_CONFIG="$OUTPUT_DIR/pipeline-config.json"
STATIC_CONFIG="$SCRIPT_DIR/config/workflow-layers.json"

if [ ! -f "$STATIC_CONFIG" ]; then
  die "Static workflow config not found: $STATIC_CONFIG"
fi

# Apply TEST_WORKFLOWS filter if set (comma-separated list of workflow paths)
if [ -n "${TEST_WORKFLOWS:-}" ]; then
  log_info "Phase 1: filtering to TEST_WORKFLOWS=$TEST_WORKFLOWS"

  IFS=',' read -ra wf_filter <<< "$TEST_WORKFLOWS"
  filter_jq='[.workflows[] | select('
  first=true
  for wf in "${wf_filter[@]}"; do
    wf=$(echo "$wf" | xargs)  # trim whitespace
    if [ "$first" = true ]; then
      filter_jq+="(.path == \"$wf\" or (.path | endswith(\"/\" + \"$wf\")))"
      first=false
    else
      filter_jq+=" or (.path == \"$wf\" or (.path | endswith(\"/\" + \"$wf\")))"
    fi
  done
  filter_jq+=')]'

  jq "{workflows: $filter_jq}" "$STATIC_CONFIG" > "$PIPELINE_CONFIG"
else
  jq '{workflows: .workflows}' "$STATIC_CONFIG" > "$PIPELINE_CONFIG"
fi

total=$(jq '.workflows | length' "$PIPELINE_CONFIG")
other_count=$(jq '[.workflows[] | select(.classification == "other")] | length' "$PIPELINE_CONFIG")
non_other_count=$(jq '[.workflows[] | select(.classification != "other")] | length' "$PIPELINE_CONFIG")

if [ "$non_other_count" -gt 0 ]; then
  non_other_names=$(jq -r '[.workflows[] | select(.classification != "other") | .path | gsub(".*/"; "")] | join(", ")' "$PIPELINE_CONFIG")
  log_warn "Phase 1: $non_other_count workflow(s) will be skipped in Phase 2 (not 'other' classification): $non_other_names"
fi

log_info "Phase 1 done: $total workflows loaded ($other_count classified as 'other')"

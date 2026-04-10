#!/usr/bin/env bash
set -euo pipefail

# Phase 1: Discovery & Mapping
#
# Builds pipeline-config.json by:
#   1. Classifying each workflow as pr-gate, merge-gate, or other
#   2. Fetching each "other" workflow YAML from tt-metal via gh api
#   3. Invoking Cursor agent to determine the test layer for each workflow

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
PIPELINE_CONFIG="$OUTPUT_DIR/pipeline-config.json"
PROMPT_TEMPLATE="$SCRIPT_DIR/prompts/classify_workflow_layer.txt"

# Workflow list from aggregate-workflow-data.yaml (lines 50-86)
WORKFLOWS=(
  ".github/workflows/sanity-tests.yaml"
  ".github/workflows/blackhole-post-commit.yaml"
  ".github/workflows/blackhole-e2e-tests.yaml"
  ".github/workflows/blackhole-demo-tests.yaml"
  ".github/workflows/galaxy-profiler-tests.yaml"
  ".github/workflows/galaxy-multi-user-isolation-tests.yaml"
  ".github/workflows/galaxy-deepseek-tests.yaml"
  ".github/workflows/galaxy-perf-tests.yaml"
  ".github/workflows/galaxy-demo-tests.yaml"
  ".github/workflows/galaxy-unit-tests.yaml"
  ".github/workflows/galaxy-integration-tests.yaml"
  ".github/workflows/galaxy-stress-tests.yaml"
  ".github/workflows/galaxy-e2e-tests.yaml"
  ".github/workflows/galaxy-sanity.yaml"
  ".github/workflows/galaxy-health.yaml"
  ".github/workflows/t3000-perf-tests.yaml"
  ".github/workflows/t3000-e2e-tests.yaml"
  ".github/workflows/t3000-integration-tests.yaml"
  ".github/workflows/t3000-profiler-tests.yaml"
  ".github/workflows/t3000-perplexity-tests.yaml"
  ".github/workflows/t3000-demo-tests.yaml"
  ".github/workflows/t3000-unit-tests.yaml"
  ".github/workflows/fast-dispatch-frequent-tests.yaml"
  ".github/workflows/perf-device-models.yaml"
  ".github/workflows/perf-models.yaml"
  ".github/workflows/fast-dispatch-full-regressions-and-models.yaml"
  ".github/workflows/single-card-demo-tests.yaml"
  ".github/workflows/tt-metal-l2-nightly.yaml"
  ".github/workflows/ttnn-run-sweeps.yaml"
  ".github/workflows/vllm-nightly-tests.yaml"
  ".github/workflows/metal-run-microbenchmarks.yaml"
  ".github/workflows/sanity-tests-debug.yaml"
  ".github/workflows/merge-gate.yaml"
  ".github/workflows/pr-gate.yaml"
)

classify_gate_level() {
  local wf="$1"
  local basename
  basename=$(basename "$wf")
  case "$basename" in
    pr-gate.yaml)    echo "pr-gate" ;;
    merge-gate.yaml) echo "merge-gate" ;;
    *)               echo "other" ;;
  esac
}

# Fetch workflow file content from tt-metal main branch via GitHub API
fetch_workflow_yaml() {
  local wf_path="$1"
  gh api "repos/${AT_OWNER_REPO}/contents/${wf_path}?ref=main" \
    --jq '.content' 2>/dev/null \
    | base64 --decode 2>/dev/null || echo ""
}

log_info "Phase 1: building pipeline config for ${#WORKFLOWS[@]} workflows"

# Initialize output
echo '{"workflows":[]}' > "$PIPELINE_CONFIG"

for wf in "${WORKFLOWS[@]}"; do
  classification=$(classify_gate_level "$wf")
  log_info "  $wf -> $classification"

  if [ "$classification" != "other" ]; then
    # Gate workflows: record but skip agent classification
    jq --arg path "$wf" \
       --arg cls "$classification" \
       '.workflows += [{"path": $path, "classification": $cls, "test_layer": "multi-layer", "agent_reasoning": "Gate workflow covers multiple layers"}]' \
       "$PIPELINE_CONFIG" > "${PIPELINE_CONFIG}.tmp" && mv "${PIPELINE_CONFIG}.tmp" "$PIPELINE_CONFIG"
    continue
  fi

  # Fetch the workflow YAML from tt-metal
  wf_content=$(fetch_workflow_yaml "$wf")
  if [ -z "$wf_content" ]; then
    log_warn "  Could not fetch $wf — marking as unknown"
    jq --arg path "$wf" \
       --arg cls "$classification" \
       '.workflows += [{"path": $path, "classification": $cls, "test_layer": "unknown", "agent_reasoning": "Could not fetch workflow YAML"}]' \
       "$PIPELINE_CONFIG" > "${PIPELINE_CONFIG}.tmp" && mv "${PIPELINE_CONFIG}.tmp" "$PIPELINE_CONFIG"
    continue
  fi

  # Ask the Cursor agent to classify this workflow
  agent_output="$(mktemp)"
  if cursor_agent_from_template "$PROMPT_TEMPLATE" "$agent_output" \
       "WORKFLOW_PATH=$wf" \
       "WORKFLOW_CONTENT=$wf_content"; then

    test_layer=$(jq -r '.test_layer // "unknown"' "$agent_output" 2>/dev/null || echo "unknown")
    confidence=$(jq -r '.confidence // "low"' "$agent_output" 2>/dev/null || echo "low")
    reasoning=$(jq -r '.reasoning // "No reasoning provided"' "$agent_output" 2>/dev/null || echo "No reasoning provided")

    log_info "    -> layer=$test_layer confidence=$confidence"
  else
    test_layer="unknown"
    confidence="low"
    reasoning="Agent call failed"
    log_warn "    -> agent classification failed, marking as unknown"
  fi

  jq --arg path "$wf" \
     --arg cls "$classification" \
     --arg layer "$test_layer" \
     --arg conf "$confidence" \
     --arg reason "$reasoning" \
     '.workflows += [{"path": $path, "classification": $cls, "test_layer": $layer, "confidence": $conf, "agent_reasoning": $reason}]' \
     "$PIPELINE_CONFIG" > "${PIPELINE_CONFIG}.tmp" && mv "${PIPELINE_CONFIG}.tmp" "$PIPELINE_CONFIG"

  rm -f "$agent_output"
done

total=$(jq '.workflows | length' "$PIPELINE_CONFIG")
other_count=$(jq '[.workflows[] | select(.classification == "other")] | length' "$PIPELINE_CONFIG")
log_info "Phase 1 done: $total workflows catalogued ($other_count classified as 'other')"

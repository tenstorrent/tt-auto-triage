#!/usr/bin/env bash
set -euo pipefail

# Phase 4: Classify & Output Bug Escapes
#
# For each fix-point entry:
#   1. Compare the fix commit layer vs the test layer
#   2. Same layer → horizontal bug escape
#   3. Fix in lower layer → vertical bug escape
#   4. Write bug-escapes-output.json with the final results
#   5. Print a summary to $GITHUB_STEP_SUMMARY (if available)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
FIX_POINTS_INPUT="$OUTPUT_DIR/fix-points.json"
BUG_ESCAPES_OUTPUT="$OUTPUT_DIR/bug-escapes-output.json"

LOOKBACK_DAYS="${LOOKBACK_DAYS:-14}"
MAX_ESCAPES="${MAX_ESCAPES:-999}"

generated_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
lookback_start=$(date -u -d "-${LOOKBACK_DAYS} days" '+%Y-%m-%d' 2>/dev/null \
  || date -u -v "-${LOOKBACK_DAYS}d" '+%Y-%m-%d' 2>/dev/null \
  || date -u '+%Y-%m-%d')
lookback_end=$(date -u '+%Y-%m-%d')

num_fixpoints=$(jq 'length' "$FIX_POINTS_INPUT")
log_info "Phase 4: classifying $num_fixpoints fix points as bug escapes"

# Build the bug_escapes array
bug_escapes="[]"

for i in $(seq 0 $((num_fixpoints - 1))); do
  fp=$(jq -c ".[$i]" "$FIX_POINTS_INPUT")

  # Extract failure info
  test_name=$(echo "$fp" | jq -r '.failure.test_name')
  test_pipeline=$(echo "$fp" | jq -r '.failure.workflow')
  test_job=$(echo "$fp" | jq -r '.failure.job')
  test_layer=$(echo "$fp" | jq -r '.failure.test_layer')
  failure_sig=$(echo "$fp" | jq -r '.failure.failure_signature')
  failing_run_ids=$(echo "$fp" | jq -c '.failure.failing_run_ids')
  last_failing_run_id=$(echo "$fp" | jq '.last_failing_run_id')
  first_passing_run_id=$(echo "$fp" | jq '.first_passing_run_id')

  # Use the highest-confidence fix commit
  fix_commit=$(echo "$fp" | jq -c '
    .candidate_fix_commits
    | sort_by(
        if .confidence == "high" then 0
        elif .confidence == "medium" then 1
        else 2 end
      )
    | .[0]
  ')

  fix_sha=$(echo "$fix_commit" | jq -r '.sha // "unknown"')
  fix_layer=$(echo "$fix_commit" | jq -r '.fix_layer // "unknown"')
  fix_message=$(echo "$fix_commit" | jq -r '.message // ""')
  fix_files=$(echo "$fix_commit" | jq -c '.files_changed // []')
  fix_reasoning=$(echo "$fix_commit" | jq -r '.reasoning // ""')

  # Classify the escape
  escape_type=$(be_classify_escape "$test_layer" "$fix_layer")

  if [ "$escape_type" = "unknown" ]; then
    log_warn "  [$((i+1))] $test_name: could not classify (test_layer=$test_layer, fix_layer=$fix_layer) — including as unknown"
  else
    log_info "  [$((i+1))] $test_name: $escape_type escape (test=$test_layer, fix=$fix_layer)"
  fi

  bug_escapes=$(echo "$bug_escapes" | jq \
    --arg type "$escape_type" \
    --arg tn "$test_name" \
    --arg tp "$test_pipeline" \
    --arg tj "$test_job" \
    --arg tl "$test_layer" \
    --arg fs "$failure_sig" \
    --argjson frid "$failing_run_ids" \
    --argjson lfr "$last_failing_run_id" \
    --argjson fpr "$first_passing_run_id" \
    --arg fsha "$fix_sha" \
    --arg fl "$fix_layer" \
    --arg fm "$fix_message" \
    --argjson ff "$fix_files" \
    --arg notes "$fix_reasoning" \
    '. += [{
      "type": $type,
      "test_name": $tn,
      "test_pipeline": $tp,
      "test_job": $tj,
      "test_layer": $tl,
      "failure_signature": $fs,
      "failing_run_ids": $frid,
      "last_failing_run_id": $lfr,
      "first_passing_run_id": $fpr,
      "fix_commit_sha": $fsha,
      "fix_commit_layer": $fl,
      "fix_commit_message": $fm,
      "fix_commit_files_changed": $ff,
      "agent_analysis_notes": $notes
    }]')

  current_count=$(echo "$bug_escapes" | jq 'length')
  if [ "$current_count" -ge "$MAX_ESCAPES" ]; then
    log_info "Reached MAX_ESCAPES=$MAX_ESCAPES — stopping classification early"
    break
  fi
done

# Write final output
jq -n \
  --arg gen "$generated_at" \
  --arg window "${lookback_start} to ${lookback_end}" \
  --argjson escapes "$bug_escapes" \
  '{
    "generated_at": $gen,
    "lookback_window": $window,
    "bug_escapes": $escapes
  }' > "$BUG_ESCAPES_OUTPUT"

# Print summary
total=$(echo "$bug_escapes" | jq 'length')
horizontal=$(echo "$bug_escapes" | jq '[.[] | select(.type == "horizontal")] | length')
vertical=$(echo "$bug_escapes" | jq '[.[] | select(.type == "vertical")] | length')
unknown_count=$(echo "$bug_escapes" | jq '[.[] | select(.type == "unknown")] | length')

log_info "Phase 4 done: $total bug escapes (horizontal=$horizontal, vertical=$vertical, unknown=$unknown_count)"

# Write GitHub Actions step summary if available
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "## Bug Escape Detection Results"
    echo ""
    echo "- **Generated**: $generated_at"
    echo "- **Lookback window**: ${lookback_start} to ${lookback_end}"
    echo "- **Total bug escapes**: $total"
    echo "  - Horizontal: $horizontal"
    echo "  - Vertical: $vertical"
    echo "  - Unknown: $unknown_count"
    echo ""

    if [ "$total" -gt 0 ]; then
      echo "### Bug Escapes"
      echo ""
      echo "| Type | Test | Test Layer | Fix Layer | Fix Commit |"
      echo "|------|------|------------|-----------|------------|"

      echo "$bug_escapes" | jq -r '.[] |
        "| \(.type) | \(.test_name) | \(.test_layer) | \(.fix_commit_layer) | \(.fix_commit_sha[:8]) |"
      '
    else
      echo "_No bug escapes detected in this window._"
    fi
  } >> "$GITHUB_STEP_SUMMARY"
fi

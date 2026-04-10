#!/usr/bin/env bash
set -euo pipefail

# Orchestrator for the bug escape detection system.
# Runs all four phases sequentially, writing intermediate results to output/.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/lib/common.sh"

# -------------------------------------------------------------------
# Pre-flight checks
# -------------------------------------------------------------------
check_command jq gh
if [ -z "${CURSOR_API_KEY:-}" ]; then
  die "CURSOR_API_KEY is not set"
fi

log_info "=== Bug Escape Detection — start ==="
log_info "Working directory: $SCRIPT_DIR"
log_info "Consecutive runs threshold: ${CONSECUTIVE_RUNS:-3}"

mkdir -p "$SCRIPT_DIR/output"

# -------------------------------------------------------------------
# Phase 1 — Discovery & Mapping
# -------------------------------------------------------------------
log_info "--- Phase 1: Discovery & Mapping ---"
bash "$SCRIPT_DIR/phase1_discover.sh"
if [ ! -f "$SCRIPT_DIR/output/pipeline-config.json" ]; then
  die "Phase 1 failed: output/pipeline-config.json not produced"
fi
log_success "Phase 1 complete — pipeline-config.json written"

# -------------------------------------------------------------------
# Phase 2 — Identify Candidate Failures
# -------------------------------------------------------------------
log_info "--- Phase 2: Identify Candidate Failures ---"
bash "$SCRIPT_DIR/phase2_candidates.sh"
if [ ! -f "$SCRIPT_DIR/output/consistent-failures.json" ]; then
  die "Phase 2 failed: output/consistent-failures.json not produced"
fi
log_success "Phase 2 complete — consistent-failures.json written"

# -------------------------------------------------------------------
# Phase 3 — Find Fix Points
# -------------------------------------------------------------------
log_info "--- Phase 3: Find Fix Points ---"
bash "$SCRIPT_DIR/phase3_fixpoints.sh"
if [ ! -f "$SCRIPT_DIR/output/fix-points.json" ]; then
  die "Phase 3 failed: output/fix-points.json not produced"
fi
log_success "Phase 3 complete — fix-points.json written"

# -------------------------------------------------------------------
# Phase 4 — Classify & Output
# -------------------------------------------------------------------
log_info "--- Phase 4: Classify & Output ---"
bash "$SCRIPT_DIR/phase4_classify.sh"
if [ ! -f "$SCRIPT_DIR/output/bug-escapes-output.json" ]; then
  die "Phase 4 failed: output/bug-escapes-output.json not produced"
fi
log_success "Phase 4 complete — bug-escapes-output.json written"

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
total=$(jq '.bug_escapes | length' "$SCRIPT_DIR/output/bug-escapes-output.json")
horizontal=$(jq '[.bug_escapes[] | select(.type == "horizontal")] | length' "$SCRIPT_DIR/output/bug-escapes-output.json")
vertical=$(jq '[.bug_escapes[] | select(.type == "vertical")] | length' "$SCRIPT_DIR/output/bug-escapes-output.json")

log_success "=== Bug Escape Detection — done ==="
log_info "Total bug escapes found: $total (horizontal=$horizontal, vertical=$vertical)"

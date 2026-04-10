#!/usr/bin/env bash
set -euo pipefail

# Orchestrator for the bug escape detection system.
# Runs phases 0-4 sequentially, writing intermediate results to output/.
#
# Scoping controls (env vars):
#   MAX_PHASE         — stop after this phase (0-4, default 4)
#   TEST_WORKFLOWS    — comma-separated workflow paths to process (default: all)
#   MAX_CANDIDATES    — max confirmed failures before Phase 2 stops (default 999)
#   MAX_LOG_BYTES     — per-run log truncation in bytes (default 50000)
#   MAX_ESCAPES       — max bug escapes before Phase 4 stops (default 999)
#   CONSECUTIVE_RUNS  — consecutive failure threshold (default 3)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/lib/common.sh"

MAX_PHASE="${MAX_PHASE:-4}"

# -------------------------------------------------------------------
# Pre-flight checks
# -------------------------------------------------------------------
check_command jq gh
if [ -z "${CURSOR_API_KEY:-}" ]; then
  die "CURSOR_API_KEY is not set"
fi

log_info "=== Bug Escape Detection — start ==="
log_info "Working directory: $SCRIPT_DIR"
log_info "MAX_PHASE=$MAX_PHASE"
log_info "CONSECUTIVE_RUNS=${CONSECUTIVE_RUNS:-3}"
log_info "MAX_CANDIDATES=${MAX_CANDIDATES:-999}"
log_info "MAX_LOG_BYTES=${MAX_LOG_BYTES:-50000}"
log_info "MAX_ESCAPES=${MAX_ESCAPES:-999}"
[ -n "${TEST_WORKFLOWS:-}" ] && log_info "TEST_WORKFLOWS=$TEST_WORKFLOWS"

mkdir -p "$SCRIPT_DIR/output"

# -------------------------------------------------------------------
# Phase 0 — Smoke Test
# -------------------------------------------------------------------
log_info "--- Phase 0: Smoke Test ---"

log_info "Checking envsubst..."
if ! command -v envsubst &>/dev/null; then
  die "envsubst is required but not found in PATH"
fi
log_info "  envsubst: ok"

log_info "Checking agent CLI..."
agent_version=$(agent --version 2>/dev/null || echo "FAILED")
if [ "$agent_version" = "FAILED" ]; then
  die "agent CLI not working (agent --version failed)"
fi
log_info "  agent version: $agent_version"

log_info "Checking gh authentication..."
gh_check=$(gh api repos/tenstorrent/tt-metal --jq '.full_name' 2>/dev/null || echo "FAILED")
if [ "$gh_check" != "tenstorrent/tt-metal" ]; then
  die "gh api check failed — cannot read tenstorrent/tt-metal (got: $gh_check)"
fi
log_info "  gh auth: ok"

log_info "Running agent smoke query..."
smoke_output="$(mktemp)"
smoke_prompt='Respond with ONLY this exact JSON, no other text: {"status": "ok"}'
if cursor_agent_json "$smoke_prompt" "$smoke_output"; then
  smoke_status=$(jq -r '.status // "missing"' "$smoke_output" 2>/dev/null || echo "parse_failed")
  if [ "$smoke_status" = "ok" ]; then
    log_success "  Agent smoke test: passed"
  else
    log_warn "  Agent returned unexpected status: $smoke_status (continuing anyway)"
  fi
else
  die "Agent smoke query failed — check CURSOR_API_KEY and agent installation"
fi
rm -f "$smoke_output"

log_success "Phase 0 complete — all pre-flight checks passed"

if [ "$MAX_PHASE" -le 0 ]; then
  log_info "MAX_PHASE=0 — stopping after smoke test"
  exit 0
fi

# -------------------------------------------------------------------
# Phase 1 — Discovery & Mapping
# -------------------------------------------------------------------
log_info "--- Phase 1: Discovery & Mapping ---"
bash "$SCRIPT_DIR/phase1_discover.sh"
if [ ! -f "$SCRIPT_DIR/output/pipeline-config.json" ]; then
  die "Phase 1 failed: output/pipeline-config.json not produced"
fi
log_success "Phase 1 complete — pipeline-config.json written"

if [ "$MAX_PHASE" -le 1 ]; then
  log_info "MAX_PHASE=$MAX_PHASE — stopping after Phase 1"
  exit 0
fi

# -------------------------------------------------------------------
# Phase 2 — Identify Candidate Failures
# -------------------------------------------------------------------
log_info "--- Phase 2: Identify Candidate Failures ---"
bash "$SCRIPT_DIR/phase2_candidates.sh"
if [ ! -f "$SCRIPT_DIR/output/consistent-failures.json" ]; then
  die "Phase 2 failed: output/consistent-failures.json not produced"
fi
log_success "Phase 2 complete — consistent-failures.json written"

if [ "$MAX_PHASE" -le 2 ]; then
  log_info "MAX_PHASE=$MAX_PHASE — stopping after Phase 2"
  exit 0
fi

# -------------------------------------------------------------------
# Phase 3 — Find Fix Points
# -------------------------------------------------------------------
log_info "--- Phase 3: Find Fix Points ---"
bash "$SCRIPT_DIR/phase3_fixpoints.sh"
if [ ! -f "$SCRIPT_DIR/output/fix-points.json" ]; then
  die "Phase 3 failed: output/fix-points.json not produced"
fi
log_success "Phase 3 complete — fix-points.json written"

if [ "$MAX_PHASE" -le 3 ]; then
  log_info "MAX_PHASE=$MAX_PHASE — stopping after Phase 3"
  exit 0
fi

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

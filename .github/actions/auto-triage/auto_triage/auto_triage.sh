#!/bin/bash
#
# Full triage driver: analyzes filtered commits and produces triage reports.
# Usage:
#   ./auto_triage.sh <workflow_name> <subjob_name> [ci-mode]
# Example:
#   ./auto_triage.sh galaxy-quick quick-wh-glx-quick

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <workflow_name> <subjob_name> [ci-mode]" >&2
    exit 1
fi

WORKFLOW="$1"
SUBJOB="$2"
CI_MODE="${3:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANON_DATA_DIR="${ROOT}/auto_triage/data"
CANON_LOGS_DIR="${ROOT}/auto_triage/logs"
CANON_OUTPUT_DIR="${ROOT}/auto_triage/output"
DATA_LINK="${ROOT}/data"
LOGS_LINK="${ROOT}/logs"
OUTPUT_LINK="${ROOT}/output"
SUMMARY_FILE="${CANON_DATA_DIR}/boundaries_summary.json"
SUBJOB_RUNS_FILE="${CANON_DATA_DIR}/subjob_runs.json"
FIND_SCRIPT="${ROOT}/modules/boundaries/find_boundaries.sh"

echo "=== Preparing auto_triage/data and auto_triage/logs ==="
mkdir -p "$CANON_DATA_DIR" "$CANON_LOGS_DIR"
rm -rf "$CANON_OUTPUT_DIR"
mkdir -p "$CANON_OUTPUT_DIR"

# Maintain convenient symlinks (./data, ./logs, ./output) pointing at canonical locations.
ln -sfn auto_triage/data "$DATA_LINK"
ln -sfn auto_triage/logs "$LOGS_LINK"
ln -sfn auto_triage/output "$OUTPUT_LINK"

cd "$ROOT"

echo "=== Verifying boundary artifacts ==="
if [ ! -s "$SUMMARY_FILE" ]; then
    echo "Error: boundaries summary not found at $SUMMARY_FILE" >&2
    ls -l "$CANON_DATA_DIR"
    exit 1
fi
if [ ! -s "$SUBJOB_RUNS_FILE" ]; then
    echo "Error: subjob_runs.json not found at $SUBJOB_RUNS_FILE" >&2
    ls -l "$CANON_DATA_DIR"
    exit 1
fi
SUMMARY_COUNT=$(jq 'if type=="array" then length else ((.runs // []) | length) end' "$SUMMARY_FILE")
FAIL_COUNT=$(jq 'if type=="array"
                 then ([.[] | select(.status != "success")] | length)
                 else ((.runs // []) | map(select(.status != "success")) | length)
                 end' "$SUBJOB_RUNS_FILE")
echo "runs recorded: $SUMMARY_COUNT"
echo "failures recorded: $FAIL_COUNT"

if ! command -v copilot >/dev/null 2>&1; then
    echo "Error: GitHub Copilot CLI is required but not found in PATH." >&2
    exit 1
fi

INSTRUCTIONS_FILE="${ROOT}/instructions_for_llm.txt"
if [ ! -f "$INSTRUCTIONS_FILE" ]; then
    echo "Error: ${INSTRUCTIONS_FILE} not found." >&2
    exit 1
fi

read -r -d '' PROMPT <<EOF || true
You are operating in a CI environment with no interactive approval. Complete the following instructions for workflow '${WORKFLOW}' and job '${SUBJOB}':

$(cat "$INSTRUCTIONS_FILE")
EOF

echo "=== Launching GitHub Copilot CLI ==="
# Ensure COPILOT_GITHUB_TOKEN is set (should be set by action.yml, but provide fallback)
# GH_TOKEN is used by bash scripts for gh api calls and should remain as github.token
if [ -z "${COPILOT_GITHUB_TOKEN:-}" ]; then
    echo "Warning: COPILOT_GITHUB_TOKEN not set, falling back to GH_TOKEN"
    export COPILOT_GITHUB_TOKEN="${GH_TOKEN:-}"
fi
# Use programmatic mode with --allow-all-tools for CI environment
copilot -p "$PROMPT" --allow-all-tools

VERIFY_SCRIPT="${ROOT}/verify_commit_metadata.sh"
if [ -x "$VERIFY_SCRIPT" ]; then
    if ! "$VERIFY_SCRIPT"; then
        exit 1
    fi
fi

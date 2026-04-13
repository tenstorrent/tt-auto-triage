#!/bin/bash
#
# build_slack_payload.sh - Build Slack JSON payload for cancellation or normal report
#
# Handles two paths:
#   1. Cancellation: when .auto_triage/cancel.json exists and should_cancel=true
#      Uses a single parameterized jq (no 8 variants)
#   2. Normal report: when slack_message.json exists
#      Uses slack_message.jq for readable formatting
#
# Writes payload to .auto_triage/slack_payload.json and appends to GITHUB_OUTPUT:
#   payload_file=...
#   has_payload=true|false
#
# Required env:
#   JOB_NAME, WORKFLOW_NAME
# Optional env:
#   MESSAGE_PATH (default: .auto_triage/output/slack_message.json)
#   AUTO_FIX_META, SLACK_TS, ALLOW_PINGS
#   GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_RUN_NUMBER
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Use - not :- so that MESSAGE_PATH="" (explicit empty) is preserved for skip-notification path
MESSAGE_PATH="${MESSAGE_PATH-.auto_triage/output/slack_message.json}"
PAYLOAD_FILE=".auto_triage/slack_payload.json"
CANCEL_FILE=".auto_triage/cancel.json"
ERROR_MSG_FILE=".auto_triage/data/error_message.txt"
SUBJOB_RUNS_FILE=".auto_triage/data/subjob_runs.json"
JOB_OWNER_FILE=".auto_triage/data/job_owner.json"
SANITY_CASE1_EXTRA_PING_ID="U0AK4BVCFM0"

RUN_URL="https://github.com/${GITHUB_REPOSITORY:-owner/repo}/actions/runs/${GITHUB_RUN_ID:-0}"
RUN_LABEL="full_report_link #${GITHUB_RUN_NUMBER:-$GITHUB_RUN_ID}"

mkdir -p "$(dirname "$PAYLOAD_FILE")"

# --- Cancellation path ---
if [ -f "$CANCEL_FILE" ]; then
  SHOULD_CANCEL=$(jq -r '.should_cancel // false' "$CANCEL_FILE")
  if [ "$SHOULD_CANCEL" = "true" ]; then
    RAW_MESSAGE=$(jq -r '.message // "Auto-triage cancelled"' "$CANCEL_FILE")
    CORE_MESSAGE_WITH_NEWLINES=$(printf '%s' "$RAW_MESSAGE" | awk '{gsub(/\\n/, "\n"); print}')

    echo "Cancellation detected:"
    printf '%s\n' "$CORE_MESSAGE_WITH_NEWLINES"

    ERROR_CONTENT=""
    [ -f "$ERROR_MSG_FILE" ] && [ -s "$ERROR_MSG_FILE" ] && ERROR_CONTENT=$(head -20 "$ERROR_MSG_FILE")

    FAILING_RUN_URL=""
    FAILING_RUN_LABEL=""
    if [ -f "$SUBJOB_RUNS_FILE" ]; then
      LATEST_FAILURE=$(jq -r '
        if type == "array" then . else (.runs // []) end
        | map(select(.status == "failure"))
        | sort_by(.run_number // 0)
        | last
        // empty
      ' "$SUBJOB_RUNS_FILE" 2>/dev/null)
      if [ -n "$LATEST_FAILURE" ] && [ "$LATEST_FAILURE" != "null" ]; then
        FAILING_RUN_URL=$(echo "$LATEST_FAILURE" | jq -r '.job_url // .run_url // ""' 2>/dev/null)
        RUN_ID=$(echo "$LATEST_FAILURE" | jq -r '.run_id // .job_id // ""' 2>/dev/null)
        if [ -n "$FAILING_RUN_URL" ] && [ "$FAILING_RUN_URL" != "null" ]; then
          [ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ] && FAILING_RUN_LABEL="Run #${RUN_ID} (latest failure)" || FAILING_RUN_LABEL="latest failure"
        fi
      fi
    fi

    # Single parameterized jq for all cancellation variants
    jq -n \
      --arg cancel_msg "$CORE_MESSAGE_WITH_NEWLINES" \
      --arg workflow "$WORKFLOW_NAME" \
      --arg job "$JOB_NAME" \
      --arg run_url "$RUN_URL" \
      --arg run_label "$RUN_LABEL" \
      --arg failing_run_url "${FAILING_RUN_URL:-}" \
      --arg failing_run_label "${FAILING_RUN_LABEL:-}" \
      --arg error_msg "${ERROR_CONTENT:-}" \
      --arg thread_ts "${SLACK_TS:-}" \
      '
        ($cancel_msg + "\n*Workflow:* " + $workflow + "\n*Job:* " + $job + "\n*Run:* <" + $run_url + "|" + $run_label + ">"
          + (if ($failing_run_url != "" and $failing_run_label != "") then "\n*FAILING RUN:* <" + $failing_run_url + "|" + $failing_run_label + ">" else "" end)
          + (if ($error_msg != "") then "\n\n*FAILURE MESSAGE:*\n```" + $error_msg + "```" else "" end)
          + "\n\n---\n_DISCLAIMER: This analysis has been done by AI. Do not take the results as absolute truth since it has been inaccurate in the past._"
        ) as $text
        | "*Auto-triage cancelled:*\n" + $text
        | if $thread_ts != "" then {text: ., thread_ts: $thread_ts} else {text: .} end
      ' > "$PAYLOAD_FILE"

    echo "payload_file=$PAYLOAD_FILE" >> "${GITHUB_OUTPUT:-/dev/null}"
    echo "has_payload=true" >> "${GITHUB_OUTPUT:-/dev/null}"
    exit 0
  fi
fi

# --- Normal report path ---
if [ -z "${MESSAGE_PATH:-}" ]; then
  echo "has_payload=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi
if [ ! -f "$MESSAGE_PATH" ]; then
  echo "Slack message file '$MESSAGE_PATH' not found; skipping notification."
  echo "has_payload=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi

# Override job/workflow names from inputs
if [ -n "${JOB_NAME:-}" ] || [ -n "${WORKFLOW_NAME:-}" ]; then
  TMP_JSON=$(mktemp)
  if [ -n "${JOB_NAME:-}" ] && [ -n "${WORKFLOW_NAME:-}" ]; then
    jq --arg job_name "$JOB_NAME" --arg workflow_name "$WORKFLOW_NAME" '.failing_job_name = $job_name | .workflow_name = $workflow_name' "$MESSAGE_PATH" > "$TMP_JSON"
  elif [ -n "${JOB_NAME:-}" ]; then
    jq --arg job_name "$JOB_NAME" '.failing_job_name = $job_name' "$MESSAGE_PATH" > "$TMP_JSON"
  else
    jq --arg workflow_name "$WORKFLOW_NAME" '.workflow_name = $workflow_name' "$MESSAGE_PATH" > "$TMP_JSON"
  fi
  mv "$TMP_JSON" "$MESSAGE_PATH"
fi

AUTO_FIX_NOTE=""
[ -n "${AUTO_FIX_META:-}" ] && [ -f "$AUTO_FIX_META" ] && AUTO_FIX_NOTE=$(jq -r '.auto_fix_pr_url // ""' "$AUTO_FIX_META" 2>/dev/null || echo "")

# Resolve group pings (S-prefixed IDs) to a random individual member
RESOLVE_SCRIPT="$SCRIPT_DIR/resolve_group_pings.py"
SLACK_DATA_DIR=".auto_triage/auto_triage/data"
if [ -f "$RESOLVE_SCRIPT" ] && [ -d "$SLACK_DATA_DIR" ]; then
  RESOLVE_FILES=""
  [ -f "$MESSAGE_PATH" ] && RESOLVE_FILES="$MESSAGE_PATH"
  [ -f "$JOB_OWNER_FILE" ] && RESOLVE_FILES="$RESOLVE_FILES $JOB_OWNER_FILE"
  if [ -n "$RESOLVE_FILES" ]; then
    python3 "$RESOLVE_SCRIPT" \
      --slack-groups "$SLACK_DATA_DIR/slack_groups.json" \
      --slack-directory "$SLACK_DATA_DIR/slack_directory.json" \
      --files $RESOLVE_FILES 2>&1 || echo "Warning: group ping resolution failed (non-fatal)"
  fi
fi

JOB_OWNER_PING=""
if [ -f "$JOB_OWNER_FILE" ]; then
  # Groups/subteams (S-prefixed IDs) are never pinged to avoid spamming entire teams
  JOB_OWNER_PING=$(jq -r --arg allow "${ALLOW_PINGS:-false}" '
    [.[] | select(.name != "") |
      if ($allow == "true") and ((.slack_id // "") != "") and ((.slack_id | startswith("S")) | not) then
        "<@" + .slack_id + ">"
      else .name
      end
    ] | join(", ")
  ' "$JOB_OWNER_FILE" 2>/dev/null || echo "")
  [ -n "$JOB_OWNER_PING" ] && echo "JOB OWNER ping string: $JOB_OWNER_PING"
fi

TEXT=$(jq -r -f "$SCRIPT_DIR/slack_message.jq" \
  --arg run_url "$RUN_URL" \
  --arg run_label "$RUN_LABEL" \
  --arg job_name "$JOB_NAME" \
  --arg workflow_name "$WORKFLOW_NAME" \
  --arg auto_fix "${AUTO_FIX_NOTE:-}" \
  --arg allow_pings "${ALLOW_PINGS:-false}" \
  --arg job_owner_ping "$JOB_OWNER_PING" \
  "$MESSAGE_PATH")

# Special-case ping for triage evaluation:
# For Case 1 in sanity-tests, prepend an explicit RELEVANT DEVELOPERS ping
# even though normal Case 1 formatting omits top-level relevant_developers.
# NOTE: the ping must appear at the START of the message — nanoclaw (BrAIn bot)
# only triggers when the message begins with its keyword. Appending at the end
# means the bot never sees the notification.
SHOULD_ADD_SANITY_CASE1_PING=$(jq -r --arg workflow_input "${WORKFLOW_NAME:-}" '
  def lc: ascii_downcase;
  (.case | tostring) as $case
  | (if ($workflow_input | length) > 0 then $workflow_input else (.workflow_name // "") end | lc) as $workflow
  | if $case == "1" and (($workflow | contains("sanity-tests"))) then
      "true"
    else
      "false"
    end
' "$MESSAGE_PATH" 2>/dev/null || echo "false")

if [ "$SHOULD_ADD_SANITY_CASE1_PING" = "true" ] && [[ "$TEXT" != *"<@${SANITY_CASE1_EXTRA_PING_ID}>"* ]]; then
  TEXT="<@${SANITY_CASE1_EXTRA_PING_ID}>"$'\n'"${TEXT}"
fi

if [ -z "$TEXT" ]; then
  echo "Parsed Slack message is empty; skipping notification."
  echo "has_payload=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi

if [ -n "${SLACK_TS:-}" ]; then
  jq -n --arg text "$TEXT" --arg thread_ts "$SLACK_TS" '{text: $text, thread_ts: $thread_ts}' > "$PAYLOAD_FILE"
else
  jq -n --arg text "$TEXT" '{text: $text}' > "$PAYLOAD_FILE"
fi

echo "payload_file=$PAYLOAD_FILE" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "has_payload=true" >> "${GITHUB_OUTPUT:-/dev/null}"

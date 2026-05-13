#!/bin/bash
#
# fetch_job_owner.sh - Fetch parent Slack thread and extract job owner @mentions
#
# Fetches the parent message via conversations.replies API, extracts text from blocks,
# calls fetch_job_owner.py to parse @mentions following the job name and resolve Slack IDs,
# writes job_owner.json. Non-fatal on failure.
#
# Required env:
#   SLACK_TS, CHANNEL_ID, SLACK_BOT_TOKEN, JOB_NAME
# Optional env:
#   JOB_OWNER_FILE (default: .regression_handling/data/job_owner.json)
#   SLACK_DATA_DIR (default: .regression_handling/regression_handling/data)
#

set -uo pipefail

JOB_OWNER_FILE="${JOB_OWNER_FILE:-.regression_handling/data/job_owner.json}"
SLACK_DATA_DIR="${SLACK_DATA_DIR:-.regression_handling/regression_handling/data}"
DEFAULT_THREAD_TEXT_FILE="${RUNNER_TEMP:-/tmp}/thread_text_${GITHUB_RUN_ID:-$$}.txt"
THREAD_TEXT_FILE="${THREAD_TEXT_FILE:-$DEFAULT_THREAD_TEXT_FILE}"

CLEANUP_THREAD_TEXT_FILE=0
if [ "${THREAD_TEXT_FILE}" = "${DEFAULT_THREAD_TEXT_FILE}" ]; then
    CLEANUP_THREAD_TEXT_FILE=1
fi

if [ "${CLEANUP_THREAD_TEXT_FILE}" -eq 1 ]; then
    trap 'rm -f "$THREAD_TEXT_FILE"' EXIT
fi
mkdir -p "$(dirname "$JOB_OWNER_FILE")"
echo '[]' > "$JOB_OWNER_FILE"

if [ -z "${SLACK_TS:-}" ] || [ -z "${CHANNEL_ID:-}" ] || [ -z "${SLACK_BOT_TOKEN:-}" ] || [ -z "${JOB_NAME:-}" ]; then
    echo "Missing Slack credentials, thread timestamp, or job name, skipping parent message fetch"
    exit 0
fi

echo "Fetching parent Slack thread (ts=${SLACK_TS}, channel=${CHANNEL_ID})..."

RESPONSE=$(curl -s -X GET \
    "https://slack.com/api/conversations.replies?channel=${CHANNEL_ID}&ts=${SLACK_TS}&limit=100&inclusive=true" \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}")

OK_STATUS=$(echo "$RESPONSE" | jq -r '.ok // false')
if [ "$OK_STATUS" != "true" ]; then
    echo "Failed to fetch parent message: $(echo "$RESPONSE" | jq -r '.error // "unknown"')"
    exit 0
fi

# Extract text from both legacy block text fields and modern rich_text blocks.
# Copied/forwarded Slack messages often store content in rich_text elements.
echo "$RESPONSE" | jq -r '
  def rich_text_to_text(elems):
    [elems[]? |
      if .type == "text" then (.text // "")
      elif .type == "link" then (.text // .url // "")
      elif .type == "emoji" then (":" + (.name // "") + ":")
      elif .type == "user" then ("<@" + (.user_id // "") + ">")
      elif .type == "usergroup" then ("<!subteam^" + (.usergroup_id // "") + ">")
      elif .type == "channel" then ("<#" + (.channel_id // "") + ">")
      elif (.elements | type) == "array" then rich_text_to_text(.elements)
      else ""
      end
    ] | join("");
  [
    .messages[] |
      if (.blocks | type) == "array" then
        [.blocks[] |
          if .type == "rich_text" then
            rich_text_to_text(.elements // [])
          elif (.text | type) == "object" then
            (.text.text // "")
          else
            (.text // "")
          end
        ] | join("\n")
      else
        (.text // "")
      end
  ] | join("\n")
' > "$THREAD_TEXT_FILE"
echo "Thread fetched ($(wc -c < "$THREAD_TEXT_FILE") bytes). Searching for job: ${JOB_NAME}"

export JOB_NAME
export JOB_OWNER_FILE
export THREAD_TEXT_FILE
export SLACK_DATA_DIR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! python3 "$SCRIPT_DIR/fetch_job_owner.py"; then
    echo "Warning: Job owner extraction failed (non-fatal), continuing without JOB OWNER field"
fi

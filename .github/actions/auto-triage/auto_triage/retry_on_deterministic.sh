#!/bin/bash
#
# Retry logic for deterministic failures. Delegates to scripts/retry_on_deterministic.sh.
#
# Usage: ./retry_on_deterministic.sh <job_name> <workflow_name> [slack_ts]
#
# Environment: SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, GH_TOKEN, GITHUB_TOKEN, COPILOT_GITHUB_TOKEN
# Outputs: RETRY_RESULT in retry_result.json; updates slack_message.json, explanation.md
#

# Validate parameters here so callers of this wrapper see a clear usage message.
if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <job_name> <workflow_name> [slack_ts]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/scripts/retry_on_deterministic.sh" "$@"

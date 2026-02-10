#!/bin/bash

# Optional auto-fix trigger. When create_PR_boolean.json:set true, invoke
# Copilot delegate to attempt a draft PR using the generated explanation.

set -euo pipefail

echo "auto fix is disabled due to issues: https://github.com/tenstorrent/tt-auto-triage/issues/3"
exit 0

if [ $# -lt 2 ]; then
    echo "Usage: $0 <workflow_name> <subjob_name>" >&2
    exit 1
fi

WORKFLOW_NAME="$1"
SUBJOB_NAME="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JSON_FLAG_FILE="${SCRIPT_DIR}/create_PR_boolean.json"
EXPLANATION_FILE="${SCRIPT_DIR}/output/explanation.md"
WORKSPACE_DIR="${SCRIPT_DIR}/workspace"

if [ ! -f "$JSON_FLAG_FILE" ]; then
    echo '{"create_PR": false}' > "$JSON_FLAG_FILE"
fi

CREATE_PR=$(jq -r '.create_PR // false' "$JSON_FLAG_FILE" 2>/dev/null || echo "false")
if [ "$CREATE_PR" != "true" ]; then
    echo "Auto-fix disabled (create_PR=false); skipping Copilot delegate run."
    exit 0
fi

if [ ! -s "$EXPLANATION_FILE" ]; then
    echo "Auto-fix requested but explanation.md is missing or empty." >&2
    exit 1
fi

if [ ! -d "$WORKSPACE_DIR/.git" ]; then
    echo "Workspace mirror missing .git directory at ${WORKSPACE_DIR}." >&2
    exit 1
fi

PROMPT_FILE="$(mktemp)"
cat <<EOF > "$PROMPT_FILE"
You are a GitHub Copilot delegate tasked with authoring a SMALL, SAFE fix for the tt-metal repository.

Workflow: ${WORKFLOW_NAME}
Job: ${SUBJOB_NAME}

Failure analysis (copied from explanation.md):

$(cat "$EXPLANATION_FILE")

Requirements:
- Work directly in this repo checkout (${WORKSPACE_DIR}).
- Create a new branch, apply the minimal changes required to fix the failure, and open a **draft PR** targeting main.
- Keep the diff under 100 lines and touch at most 3 files.
- Do not modify unrelated code.
- Use the analysis above to guide the change; if the instructions are insufficient, stop and exit.
EOF

pushd "$WORKSPACE_DIR" >/dev/null
set +e
copilot delegate pr --prompt "$(cat "$PROMPT_FILE")" --draft
STATUS=$?
set -e
popd >/dev/null
rm -f "$PROMPT_FILE"

if [ "$STATUS" -ne 0 ]; then
    echo "Copilot delegate failed (exit $STATUS). Continuing without auto-fix."
    exit 0
fi

# Extract PR URL from Copilot output (expects "https://github.com/.../pull/123")
PR_URL=$(rg -o 'https://github.com/[^ ]+/pull/[0-9]+' -m1 "$WORKSPACE_DIR/.copilot/logs/latest.log" 2>/dev/null || true)
if [ -z "$PR_URL" ]; then
    echo "Auto-fix ran but PR URL could not be detected."
    exit 0
fi

echo "Auto-fix draft PR created: $PR_URL"

# Append note to explanation.md
{
    echo ""
    echo "## Auto-Fix"
    echo "*Draft PR created automatically:* $PR_URL"
} >> "$EXPLANATION_FILE"

# Persist PR URL for Slack (JQ merge)
PR_META_DIR="${SCRIPT_DIR}/auto_triage/data"
mkdir -p "$PR_META_DIR"
PR_META_FILE="${PR_META_DIR}/auto_fix_metadata.json"
jq -n --arg url "$PR_URL" '{auto_fix_pr_url: $url}' > "$PR_META_FILE"

exit 0


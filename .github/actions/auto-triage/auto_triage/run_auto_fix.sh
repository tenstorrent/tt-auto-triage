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

# Source modular validator
source "$SCRIPT_DIR/modules/auto_fix/pr_validator.sh"

JSON_FLAG_FILE="${SCRIPT_DIR}/create_PR_boolean.json"
EXPLANATION_FILE="${SCRIPT_DIR}/output/explanation.md"
WORKSPACE_DIR="${SCRIPT_DIR}/workspace"

if ! is_auto_fix_enabled "$JSON_FLAG_FILE"; then
    log_info "Auto-fix disabled (create_PR=false); skipping Copilot delegate run."
    exit 0
fi

if ! validate_explanation_file "$EXPLANATION_FILE"; then
    die "Auto-fix requested but explanation.md is missing or empty."
fi

if ! validate_workspace "$WORKSPACE_DIR"; then
    die "Workspace mirror missing .git directory at ${WORKSPACE_DIR}."
fi

check_command copilot rg jq

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
    log_warn "Copilot delegate failed (exit $STATUS). Continuing without auto-fix."
    exit 0
fi

PR_URL=$(rg -o 'https://github.com/[^ ]+/pull/[0-9]+' -m1 "$WORKSPACE_DIR/.copilot/logs/latest.log" 2>/dev/null || true)
if [ -z "$PR_URL" ]; then
    log_warn "Auto-fix ran but PR URL could not be detected."
    exit 0
fi

log_success "Auto-fix draft PR created: $PR_URL"

{
    echo ""
    echo "## Auto-Fix"
    echo "*Draft PR created automatically:* $PR_URL"
} >> "$EXPLANATION_FILE"

PR_META_DIR="${SCRIPT_DIR}/auto_triage/data"
mkdir -p "$PR_META_DIR"
PR_META_FILE="${PR_META_DIR}/auto_fix_metadata.json"
jq -n --arg url "$PR_URL" '{auto_fix_pr_url: $url}' > "$PR_META_FILE"

exit 0

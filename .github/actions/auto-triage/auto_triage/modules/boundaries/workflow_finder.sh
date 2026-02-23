#!/bin/bash
#
# workflow_finder.sh - Resolve workflow filename to GitHub Actions workflow ID
#
# Provides find_workflow_id() to look up a workflow by name, trying .yaml/.yml
# variations. Uses lib/github_api.sh.
#
# Usage: source this file.
#

if [ -n "${_WORKFLOW_FINDER_LOADED:-}" ]; then
    return 0
fi
_WORKFLOW_FINDER_LOADED=1

_WORKFLOW_FINDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/github_api.sh
source "$_WORKFLOW_FINDER_DIR/../../lib/github_api.sh"

# Resolve a workflow name (without extension) to its numeric ID.
# Tries .yaml, .yml, .YAML, .YML. Prints the ID and returns 0 on success.
# Returns 1 and prints nothing if not found.
#
#   wf_id=$(find_workflow_id "single-card-demo-tests")
#   [ -n "$wf_id" ] || die "Workflow not found"
#
find_workflow_id() {
    local workflow_name="${1-}"
    [ -n "$workflow_name" ] || return 1

    local ext raw id
    for ext in yaml yml YAML YML; do
        raw=$(gh_api "repos/${AT_OWNER_REPO}/actions/workflows/${workflow_name}.${ext}" "")
        id=$(printf '%s' "$raw" | jq -r '.id // empty' 2>/dev/null || echo "")
        if [ -n "$id" ]; then
            echo "$id"
            return 0
        fi
    done
    return 1
}

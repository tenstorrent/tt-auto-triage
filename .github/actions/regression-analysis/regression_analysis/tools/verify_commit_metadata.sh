#!/bin/bash
#
# Verify that chosen_commit.json and alternatives.json metadata match GitHub.
# Detects "hallucinations" where LLM-generated data contradicts API results.
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHOSEN_FILE="${ROOT}/output/chosen_commit.json"
ALTS_FILE="${ROOT}/output/alternatives.json"

# shellcheck source=lib/common.sh
source "$ROOT/lib/common.sh"
# shellcheck source=modules/commit_data/commit_validator.sh
source "$ROOT/modules/commit_data/commit_validator.sh"

if [ -f "$CHOSEN_FILE" ]; then
    entry=$(cat "$CHOSEN_FILE" | jq -c '.')
    if [ -n "$entry" ] && [ "$entry" != "{}" ]; then
        validate_commit_metadata "$entry" "chosen_commit.json" 0 || exit 1
    fi
fi

if [ -f "$ALTS_FILE" ]; then
    idx=0
    while IFS= read -r entry; do
        [ -z "$entry" ] && continue
        validate_commit_metadata "$entry" "alternatives.json" "$idx" || exit 1
        idx=$((idx + 1))
    done < <(jq -c '.[]' "$ALTS_FILE" 2>/dev/null || true)
fi

exit 0

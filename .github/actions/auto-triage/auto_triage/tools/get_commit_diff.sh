#!/bin/bash
#
# Save the line-by-line diff introduced by a commit relative to its parent.
# Usage:
#   ./get_commit_diff.sh <commit_hash> [output_path]
# Default output: auto_triage/data/commit_<hash>_diff.patch

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

if [ $# -lt 1 ]; then
    die "Usage: $0 <commit_hash> [output_path]"
fi

COMMIT="$1"
OUTPUT_PATH="${2:-auto_triage/data/commit_${COMMIT}_diff.patch}"

if ! git cat-file -e "${COMMIT}^{commit}" >/dev/null 2>&1; then
    die "commit '${COMMIT}' not found."
fi

PARENT=""
if git rev-parse "${COMMIT}^" >/dev/null 2>&1; then
    PARENT="${COMMIT}^"
else
    # Root commit - compare against empty tree
    PARENT=$(git hash-object -t tree /dev/null)
fi

DATA_DIR=$(dirname "$OUTPUT_PATH")
mkdir -p "$DATA_DIR"
rm -f "$OUTPUT_PATH"

{
    echo "# Diff for commit ${COMMIT}"
    echo "# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo
    git diff --unified=5 "$PARENT" "$COMMIT"
} > "$OUTPUT_PATH"

echo "Diff saved to: $OUTPUT_PATH"

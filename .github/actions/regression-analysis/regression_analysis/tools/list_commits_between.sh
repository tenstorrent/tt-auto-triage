#!/bin/bash

# Utility script: list all commits between two SHAs (first-parent),
# optionally writing a small JSON array to a file.
#
# Usage:
#   ./list_commits_between.sh <start_commit> <end_commit> [output_file]
#
# The JSON schema is:
#   [ {"sha": "<40-char sha>", "short": "<first 8 chars>", "subject": "<commit subject>"}, ... ]
#
# If output_file is omitted, the JSON is printed to stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

if [ $# -lt 2 ]; then
  log_error "Missing required arguments"
  echo "Usage: $0 <start_commit> <end_commit> [output_file]" >&2
  exit 1
fi

START_COMMIT="$1"
END_COMMIT="$2"
OUTPUT_FILE="${3:-}"

if ! git rev-parse --verify "$START_COMMIT" >/dev/null 2>&1; then
  die "start commit '$START_COMMIT' not found"
fi

if ! git rev-parse --verify "$END_COMMIT" >/dev/null 2>&1; then
  die "end commit '$END_COMMIT' not found"
fi

# Use first-parent history and chronological order (oldest -> newest)
COMMITS=$(git rev-list --first-parent --reverse "$START_COMMIT".."$END_COMMIT" || true)

# For ranges like A..A, rev-list returns nothing; ensure END_COMMIT is present.
if ! echo "$COMMITS" | grep -qx "$END_COMMIT" 2>/dev/null; then
  if [ -n "$COMMITS" ]; then
    COMMITS="$COMMITS"$'\n'"$END_COMMIT"
  else
    COMMITS="$END_COMMIT"
  fi
fi

COMMITS=$(echo "$COMMITS" | awk 'NF')

if [ -z "$COMMITS" ]; then
  log_warn "No commits found between the provided SHAs."
  JSON='[]'
else
  JSON='[]'
  while IFS= read -r sha; do
    [ -z "$sha" ] && continue
    short_sha="${sha:0:8}"
    subject=$(git log -1 --format="%s" "$sha" 2>/dev/null || echo "")
    JSON=$(printf '%s' "$JSON" | jq \
      --arg sha "$sha" \
      --arg short "$short_sha" \
      --arg subject "$subject" \
      '. + [{sha: $sha, short: $short, subject: $subject}]')
  done <<< "$COMMITS"
fi

if [ -n "$OUTPUT_FILE" ]; then
  mkdir -p "$(dirname "$OUTPUT_FILE")"
  printf '%s
' "$JSON" > "$OUTPUT_FILE"
else
  printf '%s
' "$JSON"
fi

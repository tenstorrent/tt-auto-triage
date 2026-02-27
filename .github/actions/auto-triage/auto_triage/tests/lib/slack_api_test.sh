#!/bin/bash
#
# Smoke tests for lib/slack_api.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/lib/slack_api_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"
source "$AT_ROOT/lib/slack_api.sh"
echo "=== lib/slack_api.sh ==="

# -- format_slack_message (no network, pure function) --------------------------
assert_eq "format: text only" \
    "$(format_slack_message "Hello world")" \
    '{"text":"Hello world"}'

assert_eq "format: text + thread_ts" \
    "$(format_slack_message "Reply here" "123.456")" \
    '{"text":"Reply here","thread_ts":"123.456"}'

assert_eq "format: empty text" \
    "$(format_slack_message "" "ts")" \
    '{"text":"","thread_ts":"ts"}'

# -- send_slack_message / send_slack_thread (no credentials = skip, no crash) ---
unset SLACK_BOT_TOKEN SLACK_CHANNEL_ID CHANNEL_ID 2>/dev/null || true
assert "send_slack_message without creds (no crash)" send_slack_message "test"
assert "send_slack_thread without creds (no crash)" send_slack_thread "test" "123"

# -- summary ------------------------------------------------------------------
test_summary

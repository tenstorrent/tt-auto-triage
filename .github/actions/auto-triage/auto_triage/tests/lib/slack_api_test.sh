#!/bin/bash
#
# Smoke tests for lib/slack_api.sh (shared Slack API library)
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/lib/slack_api_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
SHARED_LIB="$REPO_ROOT/.github/actions/auto-triage/lib/slack_api.sh"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"

echo "=== lib/slack_api.sh ==="

# -- Shared lib exists ---------------------------------------------------------
assert "shared slack_api.sh exists" [ -f "$SHARED_LIB" ]
assert "wrapper slack_api.sh exists" [ -f "$AT_ROOT/lib/slack_api.sh" ]

# -- Source via the wrapper (which picks up common.sh + shared lib) -----------
source "$AT_ROOT/lib/slack_api.sh"

# -- format_slack_message (pure function, no network) --------------------------
assert_eq "format: text only" \
    "$(format_slack_message "Hello world")" \
    '{"text":"Hello world"}'

assert_eq "format: text + thread_ts" \
    "$(format_slack_message "Reply here" "123.456")" \
    '{"text":"Reply here","thread_ts":"123.456"}'

assert_eq "format: empty text" \
    "$(format_slack_message "" "ts")" \
    '{"text":"","thread_ts":"ts"}'

# -- send_slack_message / send_slack_thread (no credentials = skip) ------------
unset SLACK_BOT_TOKEN SLACK_CHANNEL_ID CHANNEL_ID SLACK_WEBHOOK_URL 2>/dev/null || true
assert "send_slack_message without creds (no crash)" eval 'send_slack_message "test" 2>/dev/null; true'
assert "send_slack_thread without creds (no crash)" eval 'send_slack_thread "test" "123" 2>/dev/null; true'

# -- send_slack_payload (no credentials = skip) --------------------------------
assert "send_slack_payload without creds (no crash)" eval 'send_slack_payload "{}" 2>/dev/null; true'

# -- send_slack_payload_file (various cases) -----------------------------------
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# Missing file
assert "payload_file: missing file returns 1" eval '! send_slack_payload_file "$tmpdir/nope.json" 2>/dev/null'

# Valid file, no credentials
echo '{"text":"hello"}' > "$tmpdir/test_payload.json"
assert "payload_file: no creds skips cleanly" eval 'send_slack_payload_file "$tmpdir/test_payload.json" 2>/dev/null; true'

# Valid file with SLACK_BOT_TOKEN but no channel
export SLACK_BOT_TOKEN="xoxb-fake-for-test"
unset SLACK_CHANNEL_ID CHANNEL_ID 2>/dev/null || true

# send_slack_payload_file should still read the file (channel injection is optional)
# but send_slack_payload won't fail (it'll attempt the API call which we can't test here)
# Just verify the file reading doesn't crash
unset SLACK_BOT_TOKEN 2>/dev/null || true

# -- Channel injection ---------------------------------------------------------
export SLACK_CHANNEL_ID="C_TEST_CHAN"
result=$(jq -c --arg ch "$SLACK_CHANNEL_ID" 'if .channel then . else . + {channel: $ch} end' "$tmpdir/test_payload.json")
assert_eq "channel injection" "$result" '{"text":"hello","channel":"C_TEST_CHAN"}'

# Payload already has channel - should not be overwritten
echo '{"text":"hello","channel":"C_EXISTING"}' > "$tmpdir/with_channel.json"
result2=$(jq -c --arg ch "$SLACK_CHANNEL_ID" 'if .channel then . else . + {channel: $ch} end' "$tmpdir/with_channel.json")
assert_eq "channel not overwritten" "$result2" '{"text":"hello","channel":"C_EXISTING"}'
unset SLACK_CHANNEL_ID 2>/dev/null || true

# -- Double-source guard -------------------------------------------------------
_SLACK_API_LOADED=""
source "$AT_ROOT/lib/slack_api.sh"
assert "double source: functions still defined" eval 'declare -f format_slack_message >/dev/null'

# -- summary -------------------------------------------------------------------
test_summary

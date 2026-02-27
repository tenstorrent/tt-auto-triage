#!/bin/bash
#
# slack_api.sh - Slack API helpers for auto-triage
#
# Provides:
#   format_slack_message(text, [thread_ts]) - Build chat.postMessage payload JSON
#   send_slack_message(text)                 - Post new message to channel
#   send_slack_thread(text, thread_ts)       - Reply to a thread
#
# Required env (for send_*): SLACK_BOT_TOKEN, SLACK_CHANNEL_ID (or CHANNEL_ID)
# Usage: source this file.
#

if [ -n "${_SLACK_API_LOADED:-}" ]; then
    return 0
fi
_SLACK_API_LOADED=1

# Source common for logging
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${_LIB_DIR}/common.sh"

# ==============================================================================
# Format message payload (returns JSON string, no channel - added by send_*)
# ==============================================================================
# format_slack_message "Hello"           -> {text: "Hello"}
# format_slack_message "Hello" "123.456" -> {text: "Hello", thread_ts: "123.456"}
format_slack_message() {
    local text="${1:-}"
    local thread_ts="${2:-}"

    if [ -n "$thread_ts" ]; then
        jq -cn --arg text "$text" --arg ts "$thread_ts" '{text: $text, thread_ts: $ts}'
    else
        jq -cn --arg text "$text" '{text: $text}'
    fi
}

# ==============================================================================
# Send message via chat.postMessage (no thread)
# ==============================================================================
send_slack_message() {
    local text="${1:-}"
    local channel="${SLACK_CHANNEL_ID:-${CHANNEL_ID:-}}"
    local payload
    payload=$(format_slack_message "$text" "" | jq -c --arg ch "$channel" '. + {channel: $ch}')

    if [ -z "${SLACK_BOT_TOKEN:-}" ] || [ -z "$channel" ]; then
        log_warn "Slack credentials not set, skipping notification"
        return 0
    fi

    log_info "Sending Slack notification..."
    local response
    response=$(curl -s -X POST "https://slack.com/api/chat.postMessage" \
        -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>&1)

    local ok
    ok=$(echo "$response" | jq -r '.ok // false' 2>/dev/null || echo "false")
    if [ "$ok" = "true" ]; then
        log_success "Slack notification sent successfully"
    else
        local err
        err=$(echo "$response" | jq -r '.error // "unknown"' 2>/dev/null || echo "unknown")
        log_warn "Slack notification failed: $err"
    fi
}

# ==============================================================================
# Send message as reply to a thread
# ==============================================================================
send_slack_thread() {
    local text="${1:-}"
    local thread_ts="${2:-}"
    local channel="${SLACK_CHANNEL_ID:-${CHANNEL_ID:-}}"
    local payload
    payload=$(format_slack_message "$text" "$thread_ts" | jq -c --arg ch "$channel" '. + {channel: $ch}')

    if [ -z "${SLACK_BOT_TOKEN:-}" ] || [ -z "$channel" ]; then
        log_warn "Slack credentials not set, skipping notification"
        return 0
    fi

    log_info "Sending Slack thread reply..."
    local response
    response=$(curl -s -X POST "https://slack.com/api/chat.postMessage" \
        -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>&1)

    local ok
    ok=$(echo "$response" | jq -r '.ok // false' 2>/dev/null || echo "false")
    if [ "$ok" = "true" ]; then
        log_success "Slack thread reply sent successfully"
    else
        local err
        err=$(echo "$response" | jq -r '.error // "unknown"' 2>/dev/null || echo "unknown")
        log_warn "Slack thread reply failed: $err"
    fi
}

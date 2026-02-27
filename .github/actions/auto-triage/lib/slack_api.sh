#!/bin/bash
#
# slack_api.sh - Universal Slack API helpers (shared across actions)
#
# Provides:
#   format_slack_message(text, [thread_ts]) - Build simple text payload JSON
#   send_slack_payload(payload_json)         - POST raw JSON via token or webhook
#   send_slack_payload_file(file_path)       - Read JSON file and POST it
#   send_slack_message(text)                 - Post simple text message to channel
#   send_slack_thread(text, thread_ts)       - Post simple text as thread reply
#
# Auth (checked in order):
#   1. SLACK_BOT_TOKEN + SLACK_CHANNEL_ID  -> chat.postMessage API
#   2. SLACK_WEBHOOK_URL                   -> incoming webhook (deprecated)
#
# Self-contained: works with or without common.sh logging.
#

if [ -n "${_SLACK_API_LOADED:-}" ]; then
    return 0
fi
_SLACK_API_LOADED=1

# ==============================================================================
# Internal logging - delegates to common.sh if available, else plain echo
# ==============================================================================
_sa_log()     { if declare -f log_info    >/dev/null 2>&1; then log_info "$@";    else echo "$*"; fi; }
_sa_warn()    { if declare -f log_warn    >/dev/null 2>&1; then log_warn "$@";    else echo "Warning: $*" >&2; fi; }
_sa_success() { if declare -f log_success >/dev/null 2>&1; then log_success "$@"; else echo "$*"; fi; }

# ==============================================================================
# format_slack_message(text, [thread_ts]) -> compact JSON
# ==============================================================================
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
# send_slack_payload(payload_json) - universal sender
#
# Expects a complete JSON string. For token auth the payload must contain
# "channel"; for webhook auth the channel is implicit in the URL.
# ==============================================================================
send_slack_payload() {
    local payload="${1:-}"

    if [ -n "${SLACK_BOT_TOKEN:-}" ]; then
        _sa_log "Sending Slack message via API..."
        local response
        response=$(curl -s -X POST "https://slack.com/api/chat.postMessage" \
            -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "$payload" 2>&1)

        local ok
        ok=$(echo "$response" | jq -r '.ok // false' 2>/dev/null || echo "false")
        if [ "$ok" = "true" ]; then
            _sa_success "Slack message sent successfully"
        else
            local err
            err=$(echo "$response" | jq -r '.error // "unknown"' 2>/dev/null || echo "unknown")
            _sa_warn "Slack API error: $err"
        fi
    elif [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
        _sa_log "Sending Slack message via webhook..."
        local response
        response=$(curl -s -X POST "$SLACK_WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "$payload" 2>&1)

        if [ "$response" = "ok" ]; then
            _sa_success "Slack webhook message sent successfully"
        else
            _sa_warn "Slack webhook error: $response"
        fi
    else
        _sa_warn "No Slack credentials (SLACK_BOT_TOKEN or SLACK_WEBHOOK_URL), skipping"
        return 0
    fi
}

# ==============================================================================
# send_slack_payload_file(file_path) - read JSON from file and send
#
# For token auth, injects "channel" from SLACK_CHANNEL_ID/CHANNEL_ID if the
# payload doesn't already contain one.
# ==============================================================================
send_slack_payload_file() {
    local file="${1:-}"
    local channel="${SLACK_CHANNEL_ID:-${CHANNEL_ID:-}}"

    if [ -z "$file" ] || [ ! -f "$file" ]; then
        _sa_warn "Payload file '${file:-}' not found, skipping notification"
        return 1
    fi

    if [ -z "${SLACK_BOT_TOKEN:-}" ] && [ -z "${SLACK_WEBHOOK_URL:-}" ]; then
        _sa_warn "No Slack credentials, skipping notification"
        return 0
    fi

    local payload
    if [ -n "$channel" ]; then
        payload=$(jq -c --arg ch "$channel" 'if .channel then . else . + {channel: $ch} end' "$file")
    else
        payload=$(jq -c '.' "$file")
    fi

    send_slack_payload "$payload"
}

# ==============================================================================
# send_slack_message(text) - post simple text to channel
# ==============================================================================
send_slack_message() {
    local text="${1:-}"
    local channel="${SLACK_CHANNEL_ID:-${CHANNEL_ID:-}}"

    if [ -z "${SLACK_BOT_TOKEN:-}" ] && [ -z "${SLACK_WEBHOOK_URL:-}" ]; then
        _sa_warn "No Slack credentials, skipping notification"
        return 0
    fi

    if [ -n "${SLACK_BOT_TOKEN:-}" ] && [ -z "$channel" ]; then
        _sa_warn "SLACK_CHANNEL_ID not set for token auth, skipping notification"
        return 0
    fi

    local payload
    payload=$(format_slack_message "$text" "")
    [ -n "$channel" ] && payload=$(echo "$payload" | jq -c --arg ch "$channel" '. + {channel: $ch}')

    send_slack_payload "$payload"
}

# ==============================================================================
# send_slack_thread(text, thread_ts) - post simple text as thread reply
# ==============================================================================
send_slack_thread() {
    local text="${1:-}"
    local thread_ts="${2:-}"
    local channel="${SLACK_CHANNEL_ID:-${CHANNEL_ID:-}}"

    if [ -z "${SLACK_BOT_TOKEN:-}" ] && [ -z "${SLACK_WEBHOOK_URL:-}" ]; then
        _sa_warn "No Slack credentials, skipping notification"
        return 0
    fi

    if [ -n "${SLACK_BOT_TOKEN:-}" ] && [ -z "$channel" ]; then
        _sa_warn "SLACK_CHANNEL_ID not set for token auth, skipping notification"
        return 0
    fi

    local payload
    payload=$(format_slack_message "$text" "$thread_ts")
    [ -n "$channel" ] && payload=$(echo "$payload" | jq -c --arg ch "$channel" '. + {channel: $ch}')

    send_slack_payload "$payload"
}

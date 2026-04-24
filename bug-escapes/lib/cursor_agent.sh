#!/usr/bin/env bash
#
# cursor_agent.sh — Wrapper for headless LLM agent invocations.
#
# Supports two backends, selected by the LLM_BACKEND env var:
#   cursor  (default) — Cursor CLI:  agent --trust --model auto -p "$prompt"
#   copilot           — Copilot CLI: copilot -p "$prompt" --allow-all-tools
#
# Provides functions for sending prompts to the agent, extracting JSON,
# and substituting variables into prompt templates.
#
# The agent runs in read-only mode (-p without --force) — it never edits files.

if [ -n "${_BUG_ESCAPES_CURSOR_AGENT_LOADED:-}" ]; then
  return 0
fi
_BUG_ESCAPES_CURSOR_AGENT_LOADED=1

CURSOR_AGENT_MAX_RETRIES="${CURSOR_AGENT_MAX_RETRIES:-2}"
CURSOR_AGENT_TIMEOUT="${CURSOR_AGENT_TIMEOUT:-300}"

_AGENT_LOG_DIR="${BUG_ESCAPES_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}/output/agent-logs"
_AGENT_CALL_SEQ=0

_save_agent_log() {
  local label="$1" raw_file="$2"
  mkdir -p "$_AGENT_LOG_DIR"
  _AGENT_CALL_SEQ=$((_AGENT_CALL_SEQ + 1))
  local safe_label
  safe_label=$(echo "$label" | tr '/ ' '__' | head -c 80)
  cp "$raw_file" "$_AGENT_LOG_DIR/${_AGENT_CALL_SEQ}_${safe_label}_raw.txt" 2>/dev/null || true
}

# cursor_agent_query <prompt_string> <output_file>
#
# Sends <prompt_string> to the Cursor CLI agent in headless print mode.
# Writes the agent's text response to <output_file>.
# Returns 0 on success, 1 on failure after retries.
cursor_agent_query() {
  local prompt="$1"
  local output_file="$2"
  local attempt=0
  local max_retries="${CURSOR_AGENT_MAX_RETRIES}"

  while [ "$attempt" -le "$max_retries" ]; do
    if [ "$attempt" -gt 0 ]; then
      log_warn "cursor_agent_query: retry $attempt/$max_retries"
      sleep $((attempt * 5))
    fi

    local exit_code=0
    local stderr_file
    stderr_file="$(mktemp)"
    if [ "${LLM_BACKEND:-cursor}" = "copilot" ]; then
      timeout "$CURSOR_AGENT_TIMEOUT" \
        copilot -p "$prompt" --allow-all-tools \
        > "$output_file" 2>"$stderr_file" || exit_code=$?
    else
      timeout "$CURSOR_AGENT_TIMEOUT" \
        agent --trust --model auto -p "$prompt" \
        > "$output_file" 2>"$stderr_file" || exit_code=$?
    fi

    if [ "$exit_code" -eq 0 ] && [ -s "$output_file" ]; then
      rm -f "$stderr_file"
      return 0
    fi

    if [ "$exit_code" -eq 124 ]; then
      log_warn "cursor_agent_query: timed out after ${CURSOR_AGENT_TIMEOUT}s"
    else
      log_warn "cursor_agent_query: failed with exit code $exit_code"
      if [ -s "$stderr_file" ]; then
        log_warn "cursor_agent_query stderr: $(head -c 500 "$stderr_file")"
      fi
    fi
    rm -f "$stderr_file"

    attempt=$((attempt + 1))
  done

  log_error "cursor_agent_query: all attempts exhausted"
  return 1
}

# cursor_agent_json <prompt_string> <output_file> [label]
#
# Like cursor_agent_query but extracts the first valid JSON object/array
# from the response. Saves raw response to agent-logs/.
cursor_agent_json() {
  local prompt="$1"
  local output_file="$2"
  local label="${3:-agent_call}"
  local raw_file
  raw_file="$(mktemp)"

  if ! cursor_agent_query "$prompt" "$raw_file"; then
    _save_agent_log "${label}_FAILED" "$raw_file"
    rm -f "$raw_file"
    return 1
  fi

  _save_agent_log "$label" "$raw_file"

  if jq '.' "$raw_file" > "$output_file" 2>/dev/null; then
    rm -f "$raw_file"
    return 0
  fi

  # Strip markdown code fences and try again
  sed -n '/^```/,/^```/{/^```/d;p}' "$raw_file" \
    | jq '.' > "$output_file" 2>/dev/null
  local status=$?

  if [ "$status" -ne 0 ] || [ ! -s "$output_file" ]; then
    log_warn "cursor_agent_json: could not extract valid JSON from agent response"
    cp "$raw_file" "$output_file"
    rm -f "$raw_file"
    return 1
  fi

  rm -f "$raw_file"
  return 0
}

# cursor_agent_from_template <template_file> <output_file> [VAR=VALUE ...]
#
# Reads a prompt template, substitutes ${KEY} placeholders with values.
# Uses in-process bash string replacement instead of envsubst to avoid
# polluting the environment (large values like LOGS_CONTENT would exceed
# ARG_MAX if exported).
cursor_agent_from_template() {
  local template_file="$1"
  local output_file="$2"
  shift 2

  if [ ! -f "$template_file" ]; then
    log_error "cursor_agent_from_template: template not found: $template_file"
    return 1
  fi

  local prompt
  prompt=$(<"$template_file")

  local var key value
  for var in "$@"; do
    key="${var%%=*}"
    value="${var#*=}"
    prompt="${prompt//\$\{${key}\}/$value}"
  done

  local label
  label="${template_file##*/}"
  label="${label%.txt}"
  cursor_agent_json "$prompt" "$output_file" "$label"
}

#!/usr/bin/env bash
#
# cursor_agent.sh — Wrapper for headless Cursor CLI agent invocations.
#
# Provides a single function `cursor_agent_query` that:
#   1. Substitutes template variables into a prompt
#   2. Invokes `cursor agent -p --output-format text`
#   3. Retries on transient failures (up to $CURSOR_AGENT_MAX_RETRIES)
#   4. Writes the agent response to the specified output file
#
# The agent runs in read-only mode (-p without --force) — it never edits files.

if [ -n "${_BUG_ESCAPES_CURSOR_AGENT_LOADED:-}" ]; then
  return 0
fi
_BUG_ESCAPES_CURSOR_AGENT_LOADED=1

CURSOR_AGENT_MAX_RETRIES="${CURSOR_AGENT_MAX_RETRIES:-2}"
CURSOR_AGENT_TIMEOUT="${CURSOR_AGENT_TIMEOUT:-300}"

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
    timeout "$CURSOR_AGENT_TIMEOUT" \
      cursor agent -p --output-format text "$prompt" \
      > "$output_file" 2>/dev/null || exit_code=$?

    if [ "$exit_code" -eq 0 ] && [ -s "$output_file" ]; then
      return 0
    fi

    if [ "$exit_code" -eq 124 ]; then
      log_warn "cursor_agent_query: timed out after ${CURSOR_AGENT_TIMEOUT}s"
    else
      log_warn "cursor_agent_query: failed with exit code $exit_code"
    fi

    attempt=$((attempt + 1))
  done

  log_error "cursor_agent_query: all attempts exhausted"
  return 1
}

# cursor_agent_json <prompt_string> <output_file>
#
# Like cursor_agent_query but asks the agent to respond in JSON and
# extracts the first valid JSON object/array from the response.
# Useful when the agent wraps JSON in markdown code fences.
cursor_agent_json() {
  local prompt="$1"
  local output_file="$2"
  local raw_file
  raw_file="$(mktemp)"

  if ! cursor_agent_query "$prompt" "$raw_file"; then
    rm -f "$raw_file"
    return 1
  fi

  # Try to extract JSON: first attempt raw parse, then strip code fences
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
# Reads a prompt template, substitutes variables, and sends it to the agent.
# Variables are passed as KEY=VALUE pairs; in the template, they appear as
# ${KEY} and are replaced via envsubst.
cursor_agent_from_template() {
  local template_file="$1"
  local output_file="$2"
  shift 2

  if [ ! -f "$template_file" ]; then
    log_error "cursor_agent_from_template: template not found: $template_file"
    return 1
  fi

  # Build the prompt by exporting the provided variables and running envsubst
  local prompt
  local var
  for var in "$@"; do
    export "${var?}"
  done

  local var_names=""
  for var in "$@"; do
    local key="${var%%=*}"
    var_names="${var_names}\${${key}} "
  done

  prompt=$(envsubst "$var_names" < "$template_file")

  cursor_agent_json "$prompt" "$output_file"
}

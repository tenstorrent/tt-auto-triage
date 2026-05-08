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

# Cap how much of each agent response is echoed live into the GitHub Actions
# job log (lines). The full response is always saved to agent-logs/ regardless
# — this only bounds the live tee so a 50KB+ JSON blob doesn't bury the rest
# of the run output. 500 lines covers the bulk of any realistic reasoning.
AGENT_LIVE_TAIL_LINES="${AGENT_LIVE_TAIL_LINES:-500}"

# How many lines of each agent response to embed in $GITHUB_STEP_SUMMARY.
# Smaller than the live cap because the summary is meant to be skimmable.
AGENT_SUMMARY_LINES="${AGENT_SUMMARY_LINES:-60}"

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

# Echo the agent response into the live GitHub Actions log, capped at
# AGENT_LIVE_TAIL_LINES. No-op outside GHA.
_emit_live_agent_response() {
  local raw_file="$1"
  [ "${GITHUB_ACTIONS:-}" != "true" ] && return 0
  [ ! -s "$raw_file" ] && return 0
  local size_bytes line_count
  size_bytes=$(wc -c < "$raw_file" 2>/dev/null | tr -d ' ' || echo 0)
  line_count=$(wc -l < "$raw_file" 2>/dev/null | tr -d ' ' || echo 0)
  echo "  --- agent response (${size_bytes} bytes, ${line_count} lines) ---" >&2
  if [ "$line_count" -le "$AGENT_LIVE_TAIL_LINES" ]; then
    cat "$raw_file" >&2
  else
    head -n "$AGENT_LIVE_TAIL_LINES" "$raw_file" >&2
    local skipped=$(( line_count - AGENT_LIVE_TAIL_LINES ))
    echo "  ... (truncated $skipped more lines — see agent-logs artifact for full response)" >&2
  fi
}

# cursor_agent_query <prompt_string> <output_file> [label]
#
# Sends <prompt_string> to the Cursor CLI agent in headless print mode.
# Writes the agent's text response to <output_file>.
# When running under GitHub Actions (GITHUB_ACTIONS=true), the response is
# also echoed (capped) into the live job log inside ::group::/::endgroup::
# markers so an operator can watch the reasoning during the run.
# The optional <label> (default: "agent_call") is shown in the group title.
# Returns 0 on success, 1 on failure after retries.
cursor_agent_query() {
  local prompt="$1"
  local output_file="$2"
  local label="${3:-agent_call}"
  local attempt=0
  local max_retries="${CURSOR_AGENT_MAX_RETRIES}"
  local backend="${LLM_BACKEND:-cursor}"

  # Write prompt to a temp file and use env -i to strip the GHA environment.
  # GHA runners set many large env vars; combined with the prompt (100-500KB
  # for typical candidate batches) the total argv+envp can exceed the 2MB
  # ARG_MAX limit, causing "Argument list too long" from /usr/bin/timeout.
  # env -i reduces envp to ~500B; bash reads the prompt file in-process before
  # calling execve(agent,...) so only the expanded value goes through kernel.
  local _prompt_file
  _prompt_file="$(mktemp)"
  printf '%s' "$prompt" > "$_prompt_file"

  # Open a GHA log group for this call so the response is visually grouped
  # under a collapsible header (call sequence is the saved-log seq + 1).
  local in_gha=0
  if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
    in_gha=1
    local prompt_bytes
    prompt_bytes=$(wc -c < "$_prompt_file" 2>/dev/null || echo 0)
    echo "::group::${backend}_agent #$((_AGENT_CALL_SEQ + 1)): ${label} (prompt ${prompt_bytes}B)" >&2
  fi

  while [ "$attempt" -le "$max_retries" ]; do
    if [ "$attempt" -gt 0 ]; then
      log_warn "${backend}_agent: retry $attempt/$max_retries"
      sleep $((attempt * 5))
    fi

    local exit_code=0
    local stderr_file
    stderr_file="$(mktemp)"
    if [ "$backend" = "copilot" ]; then
      timeout "$CURSOR_AGENT_TIMEOUT" \
        env -i HOME="$HOME" PATH="$PATH" \
          ${COPILOT_GITHUB_TOKEN:+GH_TOKEN="$COPILOT_GITHUB_TOKEN"} \
        bash -c 'copilot -p "$(cat "$1")" --allow-all-tools' -- "$_prompt_file" \
        > "$output_file" 2>"$stderr_file" || exit_code=$?
    else
      timeout "$CURSOR_AGENT_TIMEOUT" \
        env -i HOME="$HOME" PATH="$PATH" \
          ${CURSOR_API_KEY:+CURSOR_API_KEY="$CURSOR_API_KEY"} \
        bash -c 'agent --trust --model auto -p "$(cat "$1")"' -- "$_prompt_file" \
        > "$output_file" 2>"$stderr_file" || exit_code=$?
    fi

    if [ "$exit_code" -eq 0 ] && [ -s "$output_file" ]; then
      _emit_live_agent_response "$output_file"
      rm -f "$stderr_file" "$_prompt_file"
      [ "$in_gha" = "1" ] && echo "::endgroup::" >&2
      return 0
    fi

    if [ "$exit_code" -eq 124 ]; then
      log_warn "${backend}_agent: timed out after ${CURSOR_AGENT_TIMEOUT}s"
    else
      log_warn "${backend}_agent: failed with exit code $exit_code"
      if [ -s "$stderr_file" ]; then
        log_warn "${backend}_agent stderr: $(head -c 500 "$stderr_file")"
      fi
    fi
    # Surface partial output too — when the agent fails we still want to see
    # whatever it printed before exiting, since that often explains why.
    [ -s "$output_file" ] && _emit_live_agent_response "$output_file"
    rm -f "$stderr_file"

    attempt=$((attempt + 1))
  done

  rm -f "$_prompt_file"
  log_error "${backend}_agent: all attempts exhausted"
  [ "$in_gha" = "1" ] && echo "::endgroup::" >&2
  return 1
}

# cursor_agent_json <prompt_string> <output_file> [label]
#
# Like cursor_agent_query but extracts the first valid JSON object/array
# from the response. Saves raw response to agent-logs/.
# Also appends a structured trail entry to $GITHUB_STEP_SUMMARY (when set)
# so operators can browse the LLM reasoning from the run page without
# downloading the agent-logs artifact.
cursor_agent_json() {
  local prompt="$1"
  local output_file="$2"
  local label="${3:-agent_call}"
  local raw_file
  raw_file="$(mktemp)"

  if ! cursor_agent_query "$prompt" "$raw_file" "$label"; then
    _save_agent_log "${label}_FAILED" "$raw_file"
    _append_agent_summary "$label" "$raw_file" "FAILED"
    rm -f "$raw_file"
    return 1
  fi

  _save_agent_log "$label" "$raw_file"

  # Only emit the *first* JSON value — agents occasionally return multiple
  # arrays (retry output appended, or model echoed prompt + result) which
  # would make jq output multi-line counts and break downstream arithmetic.
  if jq -c '.' "$raw_file" 2>/dev/null | head -1 | jq '.' > "$output_file" 2>/dev/null \
      && [ -s "$output_file" ]; then
    _append_agent_summary "$label" "$raw_file" "ok"
    rm -f "$raw_file"
    return 0
  fi

  # Strip markdown code fences and try again (take first block only)
  sed -n '/^```/,/^```/{/^```/d;p}' "$raw_file" \
    | jq -c '.' 2>/dev/null | head -1 | jq '.' > "$output_file" 2>/dev/null
  local status=$?

  if [ "$status" -ne 0 ] || [ ! -s "$output_file" ]; then
    log_warn "cursor_agent_json: could not extract valid JSON from agent response"
    _append_agent_summary "$label" "$raw_file" "json_extract_failed"
    cp "$raw_file" "$output_file"
    rm -f "$raw_file"
    return 1
  fi

  _append_agent_summary "$label" "$raw_file" "ok_after_fence_strip"
  rm -f "$raw_file"
  return 0
}

# Append a per-call entry to $GITHUB_STEP_SUMMARY summarising one agent call.
# The full raw response is preserved in agent-logs/; this is a curated excerpt
# meant to be skimmable from the run page.
# Usage: _append_agent_summary <label> <raw_file> <status>
_append_agent_summary() {
  local label="$1" raw_file="$2" status="$3"
  [ -z "${GITHUB_STEP_SUMMARY:-}" ] && return 0
  [ ! -w "$(dirname "$GITHUB_STEP_SUMMARY")" ] 2>/dev/null && return 0
  local backend="${LLM_BACKEND:-cursor}"
  local size_bytes line_count
  size_bytes=$(wc -c < "$raw_file" 2>/dev/null | tr -d ' ' || echo 0)
  line_count=$(wc -l < "$raw_file" 2>/dev/null | tr -d ' ' || echo 0)

  # Once per run, write the section header.
  if [ -z "${_AGENT_SUMMARY_HEADER_WRITTEN:-}" ]; then
    {
      echo ""
      echo "## Agent Trail (${backend})"
      echo ""
      echo "_Per-call LLM reasoning excerpts. Full raw responses live in the bug-escapes-output artifact under \`output/agent-logs/\`._"
      echo ""
    } >> "$GITHUB_STEP_SUMMARY" 2>/dev/null || true
    _AGENT_SUMMARY_HEADER_WRITTEN=1
  fi

  {
    echo "<details>"
    echo "<summary><b>#${_AGENT_CALL_SEQ} ${label}</b> — ${backend}, ${size_bytes}B / ${line_count} lines, status: ${status}</summary>"
    echo ""
    echo '```text'
    if [ -s "$raw_file" ]; then
      head -n "$AGENT_SUMMARY_LINES" "$raw_file"
      if [ "$line_count" -gt "$AGENT_SUMMARY_LINES" ]; then
        echo ""
        echo "... (truncated $((line_count - AGENT_SUMMARY_LINES)) more lines)"
      fi
    else
      echo "(empty response)"
    fi
    echo '```'
    echo ""
    echo "</details>"
    echo ""
  } >> "$GITHUB_STEP_SUMMARY" 2>/dev/null || true
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

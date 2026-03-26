#!/bin/bash
#
# instructions_pipeline.sh — build concatenated prompts and run manifest-driven follow-ups.
#
# Usage: source after lib/config.sh (for log_* and CANON_DATA_DIR) and modules/analysis/llm_runner.sh.
#

if [ -n "${_AUTO_TRIAGE_INSTRUCTIONS_PIPELINE_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_INSTRUCTIONS_PIPELINE_LOADED=1

_IP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$_IP_DIR/common.sh"

# Trim leading / trailing whitespace (no external commands).
_ip_trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# Concatenate instruction fragments listed in a manifest into out_file.
# manifest_rel is relative to root (e.g. instructions/pipelines/main.fragments).
# Returns 1 if a listed file is missing.
build_instruction_bundle() {
    local out_file="$1"
    local root="$2"
    local manifest_rel="$3"
    local manifest="${root}/${manifest_rel}"

    if [ ! -f "$manifest" ]; then
        log_error "Instruction manifest not found: $manifest"
        return 1
    fi

    : > "$out_file"
    local line relpath abs
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"
        line="$(_ip_trim "$line")"
        [ -z "$line" ] && continue
        relpath="$line"
        abs="${root}/${relpath}"
        if [ ! -f "$abs" ]; then
            log_error "Instruction fragment missing: $abs (manifest $manifest)"
            return 1
        fi
        cat "$abs" >> "$out_file"
    done < "$manifest"
}

# Run conditional follow-up Copilot passes from followups.manifest.
# manifest_rel is relative to root (e.g. instructions/pipelines/followups.manifest).
# Trigger names must be safe shell identifiers; only declared functions are invoked.
run_instruction_followups() {
    local root="$1"
    local workflow="$2"
    local subjob="$3"
    local ci_mode="$4"
    local manifest_rel="$5"
    local manifest="${root}/${manifest_rel}"

    [ -f "$manifest" ] || return 0

    local raw line trigger relpath abs
    while IFS= read -r raw || [ -n "$raw" ]; do
        line="${raw%%#*}"
        line="$(_ip_trim "$line")"
        [ -z "$line" ] && continue

        # First shell identifier, then any whitespace, then path (rest of line; may contain spaces).
        if ! [[ "$line" =~ ^([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]+(.+)$ ]]; then
            log_warn "Skipping malformed followups.manifest line (need: trigger then whitespace then path): $line"
            continue
        fi
        trigger="${BASH_REMATCH[1]}"
        relpath="$(_ip_trim "${BASH_REMATCH[2]}")"
        if [ -z "$relpath" ]; then
            log_warn "Skipping followups.manifest line with empty path: $line"
            continue
        fi
        if ! [[ "$trigger" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
            log_warn "Skipping follow-up with invalid trigger name: $trigger"
            continue
        fi
        if ! declare -F "$trigger" >/dev/null 2>&1; then
            log_warn "Follow-up trigger not defined: $trigger (source it from lib/followup_triggers.sh)"
            continue
        fi

        abs="${root}/${relpath}"
        if ! "$trigger" "$CANON_DATA_DIR"; then
            continue
        fi
        if [ ! -f "$abs" ]; then
            log_warn "Follow-up triggered ($trigger) but instruction file missing: $abs"
            continue
        fi

        log_info "Launching GitHub Copilot CLI (follow-up: ${relpath##*/})"
        run_llm_analysis "$abs" "$workflow" "$subjob" "$ci_mode" || log_warn "Follow-up pass failed (${relpath##*/}); earlier outputs kept."
    done < "$manifest"
}

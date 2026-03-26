#!/bin/bash
#
# Concatenate instruction fragments (*.fragments) and run optional follow-up Copilot passes
# (followups.manifest). Source after config.sh + llm_runner.sh.
#

if [ -n "${_AUTO_TRIAGE_INSTRUCTIONS_PIPELINE_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_INSTRUCTIONS_PIPELINE_LOADED=1

_IP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$_IP_DIR/common.sh"

# Paths are relative to the auto_triage/ root (directory containing instructions/ and lib/).
AT_PIPELINE_FILTER_FRAGMENTS="instructions/pipelines/filter.fragments"
AT_PIPELINE_MAIN_FRAGMENTS="instructions/pipelines/main.fragments"
AT_PIPELINE_FOLLOWUPS_MANIFEST="instructions/pipelines/followups.manifest"

_trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# Concatenate files listed in manifest_rel (one relative path per line; # comments ok).
build_instruction_bundle() {
    local out_file="$1" root="$2" manifest_rel="$3"
    local manifest="${root}/${manifest_rel}"

    [[ -f "$manifest" ]] || { log_error "Missing manifest: $manifest"; return 1; }

    : > "$out_file"
    local line abs
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        line="$(_trim "$line")"
        [[ -z "$line" ]] && continue
        abs="${root}/${line}"
        if [[ ! -f "$abs" ]]; then
            log_error "Missing fragment: $abs (from $manifest)"
            return 1
        fi
        cat "$abs" >> "$out_file"
    done <"$manifest"
}

# For each followups.manifest row: trigger_name rest_of_line_is_path → if trigger(data_dir)
# succeeds, run Copilot on that instruction file. Follow-up failures are warnings only.
run_instruction_followups() {
    local root="$1" workflow="$2" subjob="$3" ci_mode="$4" manifest_rel="$5"
    local manifest="${root}/${manifest_rel}"

    [[ -f "$manifest" ]] || return 0

    local raw line trigger relpath abs
    while IFS= read -r raw || [[ -n "$raw" ]]; do
        line="${raw%%#*}"
        line="$(_trim "$line")"
        [[ -z "$line" ]] && continue

        # trigger + whitespace + path (path may contain spaces)
        if ! [[ "$line" =~ ^([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]+(.+)$ ]]; then
            log_warn "followups.manifest: bad line (want: trigger path): $line"
            continue
        fi
        trigger="${BASH_REMATCH[1]}"
        relpath="$(_trim "${BASH_REMATCH[2]}")"
        [[ -n "$relpath" ]] || continue

        if ! declare -F "$trigger" &>/dev/null; then
            log_warn "followups.manifest: unknown trigger '$trigger' (define it and source from auto_triage.sh)"
            continue
        fi

        abs="${root}/${relpath}"
        "$trigger" "$CANON_DATA_DIR" || continue
        if [[ ! -f "$abs" ]]; then
            log_warn "followups.manifest: trigger $trigger fired but missing file $abs"
            continue
        fi

        log_info "Copilot follow-up: ${relpath##*/}"
        run_llm_analysis "$abs" "$workflow" "$subjob" "$ci_mode" ||
            log_warn "Follow-up failed (${relpath##*/}); main outputs unchanged."
    done <"$manifest"
}

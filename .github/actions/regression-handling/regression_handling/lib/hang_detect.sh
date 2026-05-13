#!/bin/bash
#
# hang_detect.sh — when to run the hang follow-up Copilot pass (see followups.manifest).
#
# Source from regression_handling.sh. To add another follow-up type: define should_run_* here (or
# another sourced lib), add a line to instructions/pipelines/followups.manifest, and source
# that lib from regression_handling.sh if needed.
#

if [ -n "${_REGRESSION_HANDLING_HANG_DETECT_LOADED:-}" ]; then
    return 0
fi
_REGRESSION_HANDLING_HANG_DETECT_LOADED=1

# Returns 0 if hang follow-up should run (markers in error text or triage artifacts present).
should_run_hang_followup_analysis() {
    local d="${1:?data directory}"
    local err="${d}/error_message.txt"
    local ht="${d}/hang_triage"

    if [[ -f "$err" ]] && grep -qE '\[HANG DETECTED\]|Card hang detected' "$err" 2>/dev/null; then
        return 0
    fi
    if [[ -f "${ht}/triage_output.txt" || -f "${ht}/debug_bus_signal_groups.json" ]]; then
        return 0
    fi
    return 1
}

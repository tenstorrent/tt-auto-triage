#!/bin/bash
#
# hang_detect.sh — detect hardware-hang signal for conditional LLM instructions.
#
# Usage: source from auto_triage.sh (after config.sh sets CANON_DATA_DIR).
#
#   should_run_hang_followup_analysis <data_dir>
#     Returns 0 if a second Copilot pass (hang_stage_instructions) should run.
#

if [ -n "${_AUTO_TRIAGE_HANG_DETECT_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_HANG_DETECT_LOADED=1

should_run_hang_followup_analysis() {
    local d="${1:?data directory required}"
    local err="${d}/error_message.txt"
    local ht="${d}/hang_triage"

    if [ -f "$err" ] && grep -qE '\[HANG DETECTED\]|Card hang detected' "$err" 2>/dev/null; then
        return 0
    fi
    if [ -f "${ht}/triage_output.txt" ] || [ -f "${ht}/debug_bus_signal_groups.json" ]; then
        return 0
    fi
    return 1
}

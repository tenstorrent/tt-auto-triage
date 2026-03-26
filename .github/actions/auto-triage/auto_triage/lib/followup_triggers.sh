#!/bin/bash
#
# followup_triggers.sh — source all follow-up trigger functions used by followups.manifest.
#
# When adding a new conditional Copilot pass:
# 1. Add lib/<name>_followup.sh defining should_run_<name>_followup_analysis "$data_dir"
# 2. Source it below
# 3. Add a row to instructions/pipelines/followups.manifest
#

if [ -n "${_AUTO_TRIAGE_FOLLOWUP_TRIGGERS_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_FOLLOWUP_TRIGGERS_LOADED=1

_FT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hang_detect.sh
source "$_FT_DIR/hang_detect.sh"

#!/bin/bash
#
# slack_api.sh - Auto-triage wrapper for the shared Slack API library
#
# Sources common.sh first (for colored logging), then delegates to the
# shared lib at ../../lib/slack_api.sh. Scripts under auto_triage/ can
# continue to source this file without change.
#

if [ -n "${_SLACK_API_LOADED:-}" ]; then
    return 0
fi

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source common.sh first so its log_info/log_warn/log_success are
# available when the shared lib's _sa_log/_sa_warn/_sa_success run.
# shellcheck source=common.sh
source "${_LIB_DIR}/common.sh"

# shellcheck source=../../lib/slack_api.sh
source "${_LIB_DIR}/../../lib/slack_api.sh"

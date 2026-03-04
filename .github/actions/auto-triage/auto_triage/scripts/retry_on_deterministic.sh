#!/bin/bash
#
# Thin wrapper for deterministic retry. Sources libs and delegates to orchestrator.
#
# Usage: ./retry_on_deterministic.sh <job_name> <workflow_name> [slack_ts]
#

if [ $# -lt 2 ]; then
    echo "Usage: $0 <job_name> <workflow_name> [slack_ts]" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ORCHESTRATOR="${ROOT}/modules/retry/retry_orchestrator.sh"

exec "$ORCHESTRATOR" "$@"

#!/bin/bash
# Thin wrapper: delegate to scripts/find_boundaries.sh (canonical entry point)
# Usage: ./find_boundaries.sh <workflow_name> <subjob_name>
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/scripts/find_boundaries.sh" "$@"

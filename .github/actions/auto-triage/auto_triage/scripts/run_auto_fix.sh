#!/bin/bash
#
# scripts/run_auto_fix.sh - Thin wrapper for auto-fix
#
# Delegates to the root run_auto_fix.sh which sources modules/auto_fix/pr_validator.sh.
# Exists so all scripts have a consistent entry point under scripts/.
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

exec "$ROOT_DIR/run_auto_fix.sh" "$@"

#!/bin/bash
# Thin wrapper: delegate to modules/boundaries/find_boundaries.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/../modules/boundaries/find_boundaries.sh" "$@"

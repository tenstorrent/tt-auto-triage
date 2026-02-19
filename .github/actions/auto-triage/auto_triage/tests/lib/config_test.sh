#!/bin/bash
#
# Smoke tests for lib/config.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/lib/config_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/lib"

source "$SCRIPT_DIR/test_harness.sh"
export AUTO_TRIAGE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$LIB_DIR/config.sh"

echo "=== lib/config.sh ==="

# -- repository defaults -------------------------------------------------------
assert_eq "AT_OWNER default"      "$AT_OWNER"      "tenstorrent"
assert_eq "AT_REPO default"       "$AT_REPO"       "tt-metal"
assert_eq "AT_OWNER_REPO"         "$AT_OWNER_REPO" "tenstorrent/tt-metal"
assert_eq "AT_BASE_URL"           "$AT_BASE_URL"    "https://github.com/tenstorrent/tt-metal"

# -- numeric constants ---------------------------------------------------------
assert_eq "AT_BATCH_SIZE"         "$AT_BATCH_SIZE"     "10"
assert_eq "AT_MAX_BATCHES"        "$AT_MAX_BATCHES"    "100"
assert_eq "AT_PER_PAGE"           "$AT_PER_PAGE"       "100"
assert_eq "AT_FAILURE_LIMIT"      "$AT_FAILURE_LIMIT"  "30"

# -- feature flags -------------------------------------------------------------
assert_eq "AT_CUTOFF_COMMIT empty"  "$AT_CUTOFF_COMMIT" ""
assert_eq "AT_REUSE_DATA default"   "$AT_REUSE_DATA"    "false"

# -- env override works --------------------------------------------------------
(
    unset AT_OWNER_REPO AT_BASE_URL
    export AT_OWNER="myorg" AT_REPO="myrepo" AT_BATCH_SIZE="5" AT_CUTOFF_COMMIT="deadbeef" AT_REUSE_DATA="true"
    # Re-source to pick up overrides (reset guard first)
    unset _AUTO_TRIAGE_CONFIG_LOADED
    source "$LIB_DIR/config.sh"
    [ "$AT_OWNER" = "myorg" ] && [ "$AT_REPO" = "myrepo" ] && [ "$AT_BATCH_SIZE" = "5" ] \
      && [ "$AT_OWNER_REPO" = "myorg/myrepo" ] && [ "$AT_BASE_URL" = "https://github.com/myorg/myrepo" ] \
      && [ "$AT_CUTOFF_COMMIT" = "deadbeef" ] && [ "$AT_REUSE_DATA" = "true" ]
) && { echo "  PASS  env overrides"; _pass=$((_pass + 1)); } \
  || { echo "  FAIL  env overrides"; _fail=$((_fail + 1)); }

# -- setup_triage_dirs ---------------------------------------------------------
TMP_ROOT=$(mktemp -d)
setup_triage_dirs "$TMP_ROOT"

assert "data dir created"   test -d "$TMP_ROOT/auto_triage/data"
assert "logs dir created"   test -d "$TMP_ROOT/auto_triage/logs"
assert "output dir created" test -d "$TMP_ROOT/auto_triage/output"
assert "data symlink"       test -L "$TMP_ROOT/data"
assert "logs symlink"       test -L "$TMP_ROOT/logs"
assert "output symlink"     test -L "$TMP_ROOT/output"

rm -rf "$TMP_ROOT"

# -- double-source guard -------------------------------------------------------
(
    source "$LIB_DIR/config.sh"   # should be a no-op (already loaded)
    true
) && { echo "  PASS  double-source guard"; _pass=$((_pass + 1)); } \
  || { echo "  FAIL  double-source guard"; _fail=$((_fail + 1)); }

test_summary

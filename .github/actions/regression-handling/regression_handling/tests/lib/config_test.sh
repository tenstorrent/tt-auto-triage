#!/bin/bash
#
# Smoke tests for lib/config.sh
# Run:  cd .github/actions/regression-handling/regression_handling && ./tests/lib/config_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-handling/regression_handling"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
source "$AT_ROOT/lib/config.sh"

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
assert_eq "AT_RUN_LIMIT_WITHOUT_SUCCESS" "$AT_RUN_LIMIT_WITHOUT_SUCCESS" "100"
assert_eq "AT_SUBJOB_MISSING_CANCEL_LIMIT" "$AT_SUBJOB_MISSING_CANCEL_LIMIT" "50"

# -- feature flags -------------------------------------------------------------
assert_eq "AT_CUTOFF_COMMIT empty"  "$AT_CUTOFF_COMMIT" ""
assert_eq "AT_REUSE_DATA default"   "$AT_REUSE_DATA"    "false"

# -- env override works --------------------------------------------------------
(
    unset AT_OWNER_REPO AT_BASE_URL
    export AT_OWNER="myorg" AT_REPO="myrepo" AT_BATCH_SIZE="5" AT_CUTOFF_COMMIT="deadbeef" AT_REUSE_DATA="true"
    # Re-source to pick up overrides (reset guard first)
    unset _REGRESSION_HANDLING_CONFIG_LOADED
    source "$AT_ROOT/lib/config.sh"
    [ "$AT_OWNER" = "myorg" ] && [ "$AT_REPO" = "myrepo" ] && [ "$AT_BATCH_SIZE" = "5" ] \
      && [ "$AT_OWNER_REPO" = "myorg/myrepo" ] && [ "$AT_BASE_URL" = "https://github.com/myorg/myrepo" ] \
      && [ "$AT_CUTOFF_COMMIT" = "deadbeef" ] && [ "$AT_REUSE_DATA" = "true" ]
) && { echo "  PASS  env overrides"; _pass=$((_pass + 1)); } \
  || { echo "  FAIL  env overrides"; _fail=$((_fail + 1)); }

# -- setup_triage_dirs ---------------------------------------------------------
TMP_ROOT=$(mktemp -d)
setup_triage_dirs "$TMP_ROOT"

assert "data dir created"   test -d "$TMP_ROOT/regression_handling/data"
assert "logs dir created"   test -d "$TMP_ROOT/regression_handling/logs"
assert "output dir created" test -d "$TMP_ROOT/regression_handling/output"
assert "data symlink"       test -L "$TMP_ROOT/data"
assert "logs symlink"       test -L "$TMP_ROOT/logs"
assert "output symlink"     test -L "$TMP_ROOT/output"

rm -rf "$TMP_ROOT"

# -- double-source guard -------------------------------------------------------
(
    source "$AT_ROOT/lib/config.sh"   # should be a no-op (already loaded)
    true
) && { echo "  PASS  double-source guard"; _pass=$((_pass + 1)); } \
  || { echo "  FAIL  double-source guard"; _fail=$((_fail + 1)); }

test_summary

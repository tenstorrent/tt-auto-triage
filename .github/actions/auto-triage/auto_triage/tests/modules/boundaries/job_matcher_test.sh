#!/bin/bash
#
# Smoke tests for modules/boundaries/job_matcher.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/boundaries/job_matcher_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
source "$AT_ROOT/modules/boundaries/job_matcher.sh"

echo "=== modules/boundaries/job_matcher.sh ==="

# -- exact match ---------------------------------------------------------------
assert "exact: same name" match_subjob "yolov5x-N150-func" "yolov5x-N150-func" "single-card-demo-tests"
assert "exact: case insensitive" match_subjob "YOLOV5X-N150-FUNC" "yolov5x-N150-func" "workflow"

# -- workflow / subjob format ---------------------------------------------------
assert "workflow/subjob format" match_subjob "single-card-demo-tests / yolov5x-N150-func" "yolov5x-N150-func" "single-card-demo-tests"
assert "workflow/subjob case insensitive" match_subjob "Workflow / SubJob" "subjob" "workflow"

# -- contains -------------------------------------------------------------------
assert "contains: subjob in middle" match_subjob "some-prefix-yolov5x-N150-func-suffix" "yolov5x-N150-func" "wf"
assert "contains: subjob at start" match_subjob "yolov5x-N150-func-extra" "yolov5x-N150-func" "wf"

# -- endswith (subset of contains) ----------------------------------------------
assert "endswith" match_subjob "prefix-yolov5x-N150-func" "yolov5x-N150-func" "wf"

# -- Unicode normalization ------------------------------------------------------
# Must match find_boundaries.sh jq set: U+2013 U+2014 U+2212 etc.
assert "Unicode dash: en dash" match_subjob "yolov5x–N150–func" "yolov5x-N150-func" "wf"   # U+2013
assert "Unicode dash: em dash" match_subjob "yolov5x—N150—func" "yolov5x-N150-func" "wf"   # U+2014
assert "Unicode dash: minus sign" match_subjob "yolov5x−N150−func" "yolov5x-N150-func" "wf" # U+2212

# -- no match -------------------------------------------------------------------
assert_fails "no match: different name" eval 'match_subjob "vanilla_unet-N150-func" "yolov5x-N150-func" "wf"'
assert_fails "no match: empty job" eval 'match_subjob "" "yolov5x-N150-func" "wf"'
assert_fails "no match: empty subjob" eval 'match_subjob "yolov5x-N150-func" "" "wf"'
assert_fails "no match: partial" eval 'match_subjob "yolov5x" "yolov5x-N150-func" "wf"'
assert_fails "no match: missing args" match_subjob
assert_fails "no match: one arg only" eval 'match_subjob "yolov5x-N150-func"'

test_summary

#!/bin/bash
#
# Smoke tests for modules/boundaries/job_matcher.sh
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/boundaries/job_matcher_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# tests/modules/boundaries -> auto_triage root (three levels up)
AUTO_TRIAGE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$AUTO_TRIAGE_ROOT/tests/lib/test_harness.sh"
source "$AUTO_TRIAGE_ROOT/modules/boundaries/job_matcher.sh"

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
# U+2013 (EN DASH) and U+2014 (EM DASH) are Pd category, should normalize to '-'
assert "Unicode dash: en dash" match_subjob "yolov5x–N150–func" "yolov5x-N150-func" "wf"   # U+2013
assert "Unicode dash: em dash" match_subjob "yolov5x—N150—func" "yolov5x-N150-func" "wf"   # U+2014

# -- no match -------------------------------------------------------------------
assert_fails "no match: different name" eval 'match_subjob "vanilla_unet-N150-func" "yolov5x-N150-func" "wf"'
assert_fails "no match: empty job" eval 'match_subjob "" "yolov5x-N150-func" "wf"'
assert_fails "no match: empty subjob" eval 'match_subjob "yolov5x-N150-func" "" "wf"'
assert_fails "no match: partial" eval 'match_subjob "yolov5x" "yolov5x-N150-func" "wf"'

test_summary

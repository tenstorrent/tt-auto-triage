#!/bin/bash
#
# Smoke tests for lib/github_api.sh
#
# Tests the structural parts (function existence, arg handling) and uses a
# mock `gh` script for API-dependent functions so we don't need real auth.
#
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/lib/github_api_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIB_DIR="$ROOT_DIR/lib"

source "$SCRIPT_DIR/test_harness.sh"
export AUTO_TRIAGE_ROOT="$ROOT_DIR"
source "$LIB_DIR/github_api.sh"

# -- create mock gh CLI --------------------------------------------------------
MOCK_DIR=$(mktemp -d)
cat > "$MOCK_DIR/gh" <<'MOCKSCRIPT'
#!/bin/bash
# Minimal mock: returns canned JSON for known endpoints, error for unknown.
endpoint=""
method="GET"
jq_expr=""
include_headers=false

while [ $# -gt 0 ]; do
    case "$1" in
        api)        shift; continue ;;
        --method)   method="$2"; shift 2; continue ;;
        --jq)       jq_expr="$2"; shift 2; continue ;;
        -H)         shift 2; continue ;;
        -i)         include_headers=true; shift; continue ;;
        *)          endpoint="$1"; shift ;;
    esac
done

case "$endpoint" in
    repos/tenstorrent/tt-metal/actions/workflows/ci.yml)
        echo '{"id":12345,"name":"ci"}' ;;
    repos/tenstorrent/tt-metal/actions/workflows/ci.yaml)
        echo '{"id":12345,"name":"ci"}' ;;
    repos/tenstorrent/tt-metal/actions/workflows/nope.yml)
        echo '{"message":"Not Found"}' ; exit 1 ;;
    repos/tenstorrent/tt-metal/commits/abc123)
        json='{"sha":"abc123","author":{"login":"testuser"}}'
        if [ -n "$jq_expr" ]; then
            echo "$json" | jq -r "$jq_expr" 2>/dev/null
        else
            echo "$json"
        fi ;;
    repos/tenstorrent/tt-metal/actions/jobs/999)
        echo '{"id":999,"name":"test-job","status":"completed"}' ;;
    repos/tenstorrent/tt-metal/actions/runs/555)
        echo '{"id":555,"status":"completed","conclusion":"failure"}' ;;
    repos/tenstorrent/tt-metal/actions/runs/555/jobs*)
        echo '{"jobs":[{"id":999,"name":"test-job"}]}' ;;
    repos/tenstorrent/tt-metal/check-runs/777/annotations*)
        echo '[]' ;;
    *)
        if $include_headers; then
            echo "HTTP/2 201"
            echo '{"message":"ok"}'
        else
            echo '{"message":"Not Found"}'
            exit 1
        fi ;;
esac
MOCKSCRIPT
chmod +x "$MOCK_DIR/gh"
export PATH="$MOCK_DIR:$PATH"

# Now source the API lib (it will find our mock gh)
source "$LIB_DIR/github_api.sh"

echo "=== lib/github_api.sh ==="

# -- gh_api / gh_api_jq -------------------------------------------------------
result=$(gh_api "repos/tenstorrent/tt-metal/commits/abc123")
assert_eq "gh_api returns JSON" "$(echo "$result" | jq -r .sha)" "abc123"

result=$(gh_api "repos/nonexistent/endpoint/404" "fallback_val")
assert_eq "gh_api fallback on error" "$result" "fallback_val"

result=$(gh_api_jq "repos/tenstorrent/tt-metal/commits/abc123" '.author.login // empty' "")
assert_eq "gh_api_jq extracts field" "$result" "testuser"

# -- get_workflow_id -----------------------------------------------------------
wf_id=$(get_workflow_id "ci.yml")
assert_eq "get_workflow_id" "$wf_id" "12345"

assert_fails "get_workflow_id unknown" get_workflow_id "nope.yml"

# -- get_job_info / get_run_info -----------------------------------------------
job=$(get_job_info 999)
assert_eq "get_job_info" "$(echo "$job" | jq -r .name)" "test-job"

run=$(get_run_info 555)
assert_eq "get_run_info" "$(echo "$run" | jq -r .conclusion)" "failure"

# -- get_jobs_for_run ----------------------------------------------------------
jobs=$(get_jobs_for_run 555)
assert_eq "get_jobs_for_run" "$(echo "$jobs" | jq -r '.jobs[0].name')" "test-job"

# -- get_commit_author ---------------------------------------------------------
author=$(get_commit_author "abc123")
assert_eq "get_commit_author" "$author" "testuser"

# -- gh_api_post ---------------------------------------------------------------
resp=$(gh_api_post "repos/tenstorrent/tt-metal/actions/jobs/123/rerun")
status=$(echo "$resp" | head -1 | awk '{print $2}')
assert_eq "gh_api_post status" "$status" "201"

# -- get_check_annotations (empty) --------------------------------------------
anns=$(get_check_annotations 777)
assert_eq "get_check_annotations empty" "$anns" "[]"

# -- cleanup -------------------------------------------------------------------
rm -rf "$MOCK_DIR"

test_summary

#!/bin/bash
#
# Smoke tests for modules/retry/run_trigger.sh
#
# Uses a mock gh CLI to avoid real API calls. Tests trigger_retry_run and
# wait_for_run_completion.
#
# Run:  cd .github/actions/auto-triage/auto_triage && ./tests/modules/retry/run_trigger_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"

# -- create mock gh CLI --------------------------------------------------------
MOCK_DIR=$(mktemp -d)
trap 'rm -rf "$MOCK_DIR"' EXIT

cat > "$MOCK_DIR/gh" <<'MOCKSCRIPT'
#!/bin/bash
endpoint=""
method="GET"
include_headers=false

while [ $# -gt 0 ]; do
    case "$1" in
        api)        shift; continue ;;
        --method)   method="$2"; shift 2; continue ;;
        --jq)       shift 2; continue ;;
        -H)         shift 2; continue ;;
        -i)         include_headers=true; shift; continue ;;
        *)          endpoint="$1"; shift ;;
    esac
done

case "$endpoint" in
    # Rerun: 201 for valid job, 404 for job 0
    repos/tenstorrent/tt-metal/actions/jobs/12345/rerun|repos/tenstorrent/tt-metal/actions/jobs/123/rerun)
        if $include_headers; then
            echo "HTTP/2 201"; echo ''
        else
            exit 1
        fi ;;
    repos/tenstorrent/tt-metal/actions/jobs/0/rerun)
        if $include_headers; then
            echo "HTTP/2 404"; echo '{"message":"Not Found"}'
        else
            exit 1
        fi ;;
    # Run info: run_attempt 2 (so wait sees "new" attempt immediately)
    repos/tenstorrent/tt-metal/actions/runs/555)
        echo '{"id":555,"status":"completed","conclusion":"success","run_attempt":2}' ;;
    repos/tenstorrent/tt-metal/actions/runs/555/attempts/2/jobs*)
        echo '{"jobs":[{"id":999,"name":"my-retry-job","status":"completed","conclusion":"success"}]}' ;;
    # Job 999: completed successfully
    repos/tenstorrent/tt-metal/actions/jobs/999)
        echo '{"id":999,"name":"my-retry-job","status":"completed","conclusion":"success"}' ;;
    *)
        if $include_headers; then
            echo "HTTP/2 201"; echo ''
        else
            echo '{"message":"Not Found"}'; exit 1
        fi ;;
esac
MOCKSCRIPT
chmod +x "$MOCK_DIR/gh"
export PATH="$MOCK_DIR:$PATH"

# Source run_trigger (pulls in github_api, config)
source "$AT_ROOT/modules/retry/run_trigger.sh"

echo "=== modules/retry/run_trigger.sh ==="

# -- trigger_retry_run ---------------------------------------------------------
assert       "trigger_retry_run: success (201)" trigger_retry_run 12345
assert       "trigger_retry_run: success (123)" trigger_retry_run 123
assert_fails "trigger_retry_run: failure (404)" eval 'trigger_retry_run 0'
assert_fails "trigger_retry_run: empty job_id"  eval 'trigger_retry_run ""'

# -- wait_for_run_completion ---------------------------------------------------
# Mock returns run_attempt 2, jobs with my-retry-job completed -> success
status=$(wait_for_run_completion 555 "my-retry-job" 1 300 5)
assert_eq "wait_for_run_completion: returns success" "$status" "success"

# -- required args -------------------------------------------------------------
status3=$(wait_for_run_completion "" "job" 1 5 1 2>/dev/null) || true
assert_eq "wait_for_run_completion: empty run_id" "$status3" "error"

test_summary

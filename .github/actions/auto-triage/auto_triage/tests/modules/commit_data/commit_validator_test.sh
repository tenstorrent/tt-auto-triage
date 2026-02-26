#!/bin/bash
#
# Tests for modules/commit_data/commit_validator.sh
#
# Tests validation logic with mock GitHub API responses.
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/commit_data/commit_validator_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

_d="$(cd "$SCRIPT_DIR" && pwd)"
while [ "$_d" != "/" ]; do [ -f "$_d/testing_lib_files/test_harness.sh" ] && . "$_d/testing_lib_files/test_harness.sh" && break; _d="${_d%/*}"; done
export AUTO_TRIAGE_ROOT="$ROOT_DIR"

# -- create mock gh CLI --------------------------------------------------------
MOCK_DIR=$(mktemp -d)
cleanup() { rm -rf "$MOCK_DIR"; }
trap cleanup EXIT

cat > "$MOCK_DIR/gh" <<'MOCKSCRIPT'
#!/bin/bash
endpoint=""
jq_arg=""
while [ $# -gt 0 ]; do
    case "$1" in
        api) shift; continue ;;
        -H) shift 2; continue ;;
        --jq) jq_arg="$2"; shift 2; continue ;;
        *) endpoint="$1"; shift ;;
    esac
done
json="{}"
case "$endpoint" in
    repos/tenstorrent/tt-metal/commits/*/pulls)
        json='[{"number":42}]' ;;
    repos/tenstorrent/tt-metal/commits/*)
        json='{"author":{"login":"commit-author"}}' ;;
    repos/tenstorrent/tt-metal/pulls/*/reviews)
        json='[{"user":{"login":"approver1"},"state":"APPROVED"},{"user":{"login":"approver2"},"state":"APPROVED"}]' ;;
    *)
        json='{}' ;;
esac
if [ -n "$jq_arg" ]; then
    echo "$json" | jq -r "$jq_arg" 2>/dev/null || echo ""
else
    echo "$json"
fi
MOCKSCRIPT
chmod +x "$MOCK_DIR/gh"
export PATH="$MOCK_DIR:$PATH"

# -- source module -------------------------------------------------------------
# shellcheck source=../../../modules/commit_data/commit_validator.sh
source "$ROOT_DIR/modules/commit_data/commit_validator.sh"

echo "=== modules/commit_data/commit_validator.sh ==="

# -- validate_commit_metadata exists -------------------------------------------
assert "validate_commit_metadata is defined" type validate_commit_metadata &>/dev/null

# -- valid entry passes --------------------------------------------------------
VALID_ENTRY='{"commit":"abc123","commit_url":"https://github.com/tenstorrent/tt-metal/commit/abc123","authors":[{"login":"commit-author","name":"A","is_org_member":true}],"approvers":[{"login":"approver1","name":"B","is_org_member":true},{"login":"approver2","name":"C","is_org_member":false}]}'
assert "valid entry passes" validate_commit_metadata "$VALID_ENTRY" "test.json" 0

# -- missing commit SHA fails --------------------------------------------------
assert_fails "missing commit fails" validate_commit_metadata '{"authors":[]}' "test.json" 0

# -- wrong commit_url fails ----------------------------------------------------
WRONG_URL='{"commit":"abc123","commit_url":"https://github.com/wrong/repo/commit/abc123","authors":[{"login":"commit-author"}],"approvers":[{"login":"approver1"}]}'
assert_fails "wrong commit_url fails" validate_commit_metadata "$WRONG_URL" "test.json" 0

# -- author missing from authors fails -----------------------------------------
NO_AUTHOR='{"commit":"abc123","authors":[{"login":"other-user"}],"approvers":[{"login":"approver1"}]}'
assert_fails "author missing from authors fails" validate_commit_metadata "$NO_AUTHOR" "test.json" 0

test_summary

#!/bin/bash
#
# Tests for modules/commit_data/batch_downloader.sh
#
# Tests batch processing logic and caching behavior using a mock gh CLI.
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/commit_data/batch_downloader_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/auto-triage/auto_triage"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export AUTO_TRIAGE_ROOT="$AT_ROOT"

# -- create mock gh CLI --------------------------------------------------------
MOCK_DIR=$(mktemp -d)
cleanup() { rm -rf "$MOCK_DIR"; }
trap cleanup EXIT

cat > "$MOCK_DIR/gh" <<'MOCKSCRIPT'
#!/bin/bash
endpoint=""
method="GET"
jq_expr=""
while [ $# -gt 0 ]; do
    case "$1" in
        api)        shift; continue ;;
        --method)   method="$2"; shift 2; continue ;;
        --jq)       jq_expr="$2"; shift 2; continue ;;
        -H)         shift 2; continue ;;
        -i)         shift; continue ;;
        *)          endpoint="$1"; shift ;;
    esac
done

case "$endpoint" in
    users/*)
        echo '{"login":"'$(echo "$endpoint" | sed 's|users/||')'","name":"Test User"}' ;;
    orgs/tenstorrent/members/*)
        echo '{}' ;;  # 200 = member
    repos/tenstorrent/tt-metal/pulls/*/reviews)
        echo '[{"user":{"login":"approver1"},"state":"APPROVED"},{"user":{"login":"copilot-pull-request-reviewer"},"body":"## Pull Request Overview\n\nSome overview text.\n\n### Reviewed Changes"}]' ;;
    repos/tenstorrent/tt-metal/pulls/*)
        pr_num=$(echo "$endpoint" | sed 's|.*pulls/||' | sed 's|/.*||')
        echo '{"title":"Test PR","html_url":"https://github.com/tenstorrent/tt-metal/pull/'$pr_num'","body":"","user":{"login":"pr-author"}}' ;;
    repos/tenstorrent/tt-metal/commits/*)
        sha=$(echo "$endpoint" | sed 's|.*commits/||')
        echo '{"sha":"'$sha'","author":{"login":"commit-author"},"commit":{"author":{"name":"Commit Author"}}}' ;;
    *)
        echo '{"message":"Not Found"}'
        exit 1 ;;
esac
MOCKSCRIPT
chmod +x "$MOCK_DIR/gh"
export PATH="$MOCK_DIR:$PATH"

# -- create temp git repo ------------------------------------------------------
GIT_DIR=$(mktemp -d)
trap "rm -rf $MOCK_DIR $GIT_DIR" EXIT
cd "$GIT_DIR"
git init -q
git config user.email "test@test.com"
git config user.name "Test"
echo "first" > f && git add f && git commit -q -m "first"
echo "second" > g && git add g && git commit -q -m "Merge (#42) from feature branch"
START=$(git rev-parse HEAD~1)
END=$(git rev-parse HEAD)

# -- source module -------------------------------------------------------------
# shellcheck source=../../../modules/commit_data/batch_downloader.sh
source "$AT_ROOT/modules/commit_data/batch_downloader.sh"

echo "=== modules/commit_data/batch_downloader.sh ==="

# -- download_commit_batch exists ----------------------------------------------
assert "download_commit_batch is defined" type download_commit_batch &>/dev/null

# -- run batch -----------------------------------------------------------------
OUTPUT_JSON="$GIT_DIR/out.json"
download_commit_batch "$START" "$END" 0 "$OUTPUT_JSON"

assert "output file created" [ -f "$OUTPUT_JSON" ]
count=$(jq 'length' "$OUTPUT_JSON")
assert_eq "one commit entry" "$count" "1"

entry=$(jq '.[0]' "$OUTPUT_JSON")
assert_eq "commit_short present" "$(echo "$entry" | jq -r '.commit_short')" "${END:0:8}"
assert_eq "pr_number" "$(echo "$entry" | jq -r '.pr_number')" "42"
assert_eq "pr_title" "$(echo "$entry" | jq -r '.pr_title')" "Test PR"
overview=$(echo "$entry" | jq -r '.copilot_overview')
assert "copilot_overview contains expected text" echo "$overview" | grep -q "Some overview text"
assert "authors array" [ "$(echo "$entry" | jq -r '.authors | length')" -ge 1 ]
assert "approvers array" [ "$(echo "$entry" | jq -r '.approvers | length')" -ge 1 ]

# -- invalid batch index fails -------------------------------------------------
assert_fails "invalid batch index" download_commit_batch "$START" "$END" 99 "$OUTPUT_JSON"

test_summary

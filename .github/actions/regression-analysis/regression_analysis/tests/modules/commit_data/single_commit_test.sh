#!/bin/bash
#
# Tests for modules/commit_data/single_commit.sh
#
# Tests single commit download using a mock gh CLI.
#
# Run: cd .github/actions/regression-analysis/regression_analysis && ./tests/modules/commit_data/single_commit_test.sh
#

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
AT_ROOT="$REPO_ROOT/.github/actions/regression-analysis/regression_analysis"
source "$REPO_ROOT/testing_lib_files/test_harness.sh"
export REGRESSION_ANALYSIS_ROOT="$AT_ROOT"

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
        echo '[{"user":{"login":"approver1"},"state":"APPROVED"},{"user":{"login":"copilot-pull-request-reviewer"},"body":"## Pull Request Overview\n\nSingle commit overview.\n\n### Reviewed Changes"}]' ;;
    repos/tenstorrent/tt-metal/pulls/*)
        pr_num=$(echo "$endpoint" | sed 's|.*pulls/||' | sed 's|/.*||')
        echo '{"title":"Single Commit PR","html_url":"https://github.com/tenstorrent/tt-metal/pull/'$pr_num'","body":"","user":{"login":"pr-author"}}' ;;
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
echo "second" > g && git add g && git commit -q -m "Merge (#99) from feature branch"
SINGLE_COMMIT=$(git rev-parse HEAD)

# -- source module -------------------------------------------------------------
# shellcheck source=../../../modules/commit_data/single_commit.sh
source "$AT_ROOT/modules/commit_data/single_commit.sh"

echo "=== modules/commit_data/single_commit.sh ==="

# -- download_single_commit exists ----------------------------------------------
assert "download_single_commit is defined" type download_single_commit &>/dev/null

# -- run single commit download -------------------------------------------------
OUTPUT_JSON="$GIT_DIR/out.json"
download_single_commit "$SINGLE_COMMIT" "$OUTPUT_JSON"

assert "output file created" [ -f "$OUTPUT_JSON" ]
count=$(jq 'length' "$OUTPUT_JSON")
assert_eq "one commit entry" "$count" "1"

entry=$(jq '.[0]' "$OUTPUT_JSON")
assert_eq "commit_short present" "$(echo "$entry" | jq -r '.commit_short')" "${SINGLE_COMMIT:0:8}"
assert_eq "pr_number" "$(echo "$entry" | jq -r '.pr_number')" "99"
assert_eq "pr_title" "$(echo "$entry" | jq -r '.pr_title')" "Single Commit PR"
overview=$(echo "$entry" | jq -r '.copilot_overview')
assert "copilot_overview contains expected text" echo "$overview" | grep -q "Single commit overview"
assert "authors array" [ "$(echo "$entry" | jq -r '.authors | length')" -ge 1 ]
assert "approvers array" [ "$(echo "$entry" | jq -r '.approvers | length')" -ge 1 ]

# -- invalid commit fails (empty sha) -------------------------------------------
assert_fails "empty commit_sha" download_single_commit "" "$OUTPUT_JSON"

test_summary

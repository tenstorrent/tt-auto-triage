#!/bin/bash
#
# Tests for modules/commit_data/download_commits.sh
#
# Tests orchestration logic: single batch vs multi-batch, commit count edge cases.
#
# Run: cd .github/actions/auto-triage/auto_triage && ./tests/modules/commit_data/download_commits_test.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

source "$SCRIPT_DIR/../../lib/test_harness.sh"
export AUTO_TRIAGE_ROOT="$ROOT_DIR"

# -- create mock gh CLI --------------------------------------------------------
MOCK_DIR=$(mktemp -d)
cleanup() { rm -rf "$MOCK_DIR"; }
trap cleanup EXIT

cat > "$MOCK_DIR/gh" <<'MOCKSCRIPT'
#!/bin/bash
endpoint=""
while [ $# -gt 0 ]; do
    case "$1" in
        api) shift; continue ;;
        --method) shift 2; continue ;;
        --jq) shift 2; continue ;;
        -H) shift 2; continue ;;
        -i) shift; continue ;;
        *) endpoint="$1"; shift ;;
    esac
done
case "$endpoint" in
    users/*) echo '{"login":"u","name":"Test"}' ;;
    orgs/tenstorrent/members/*) echo '{}' ;;
    repos/tenstorrent/tt-metal/pulls/*/reviews)
        echo '[{"user":{"login":"a1"},"state":"APPROVED"},{"user":{"login":"copilot-pull-request-reviewer"},"body":"## Pull Request Overview\n\nX"}]' ;;
    repos/tenstorrent/tt-metal/pulls/*)
        n=$(echo "$endpoint" | sed 's|.*pulls/||' | sed 's|/.*||')
        echo '{"title":"PR","html_url":"https://github.com/tenstorrent/tt-metal/pull/'$n'","body":"","user":{"login":"x"}}' ;;
    repos/tenstorrent/tt-metal/commits/*)
        echo '{"sha":"x","author":{"login":"y"},"commit":{"author":{"name":"Y"}}}' ;;
    *) echo '{}' ;;
esac
MOCKSCRIPT
chmod +x "$MOCK_DIR/gh"
export PATH="$MOCK_DIR:$PATH"

# -- source module -------------------------------------------------------------
# shellcheck source=../../../modules/commit_data/download_commits.sh
source "$ROOT_DIR/modules/commit_data/download_commits.sh"

echo "=== modules/commit_data/download_commits.sh ==="

# -- create temp git repo ------------------------------------------------------
GIT_DIR=$(mktemp -d)
trap "rm -rf $MOCK_DIR $GIT_DIR" EXIT
cd "$GIT_DIR"
git init -q
git config user.email "t@t.com"
git config user.name "T"
echo "a" > a && git add a && git commit -q -m "a"
for i in $(seq 1 5); do
    echo "$i" > "f$i" && git add "f$i" && git commit -q -m "Merge (#$((100+i)))"
done
START=$(git rev-parse HEAD~5)
END=$(git rev-parse HEAD)

# -- download_commits_between exists --------------------------------------------
assert "download_commits_between is defined" type download_commits_between &>/dev/null

# -- single batch (<= 10 commits) returns 0 ------------------------------------
OUT1="$GIT_DIR/out1.json"
download_commits_between "$START" "$END" "$OUT1" || ret=$?
assert "single batch completes" [ -f "$OUT1" ]
assert "output has entries" [ "$(jq 'length' "$OUT1")" -ge 1 ]

# -- start=end (single commit in range) -----------------------------------------
OUT_SINGLE="$GIT_DIR/out_single.json"
download_commits_between "$END" "$END" "$OUT_SINGLE" || true
assert "single commit in range produces output" [ -f "$OUT_SINGLE" ]

# -- invalid start fails -------------------------------------------------------
assert_fails "invalid start commit" download_commits_between "badsha" "$END" "$OUT1"

test_summary

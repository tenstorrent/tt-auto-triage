#!/bin/bash
#
# batch_downloader.sh - Batch download commit metadata (PR info, authors, Copilot overview)
#
# Provides download_commit_batch(start_commit, end_commit, batch_idx, output_file).
# Uses lib/github_api.sh for API calls. Caches user names and org membership.
#
# Usage: source this file, then call download_commit_batch
#

if [ -n "${_BATCH_DOWNLOADER_LOADED:-}" ]; then
    return 0
fi
_BATCH_DOWNLOADER_LOADED=1

_BD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/github_api.sh
source "$_BD_DIR/../../lib/github_api.sh"

BATCH_SIZE="${AT_BATCH_SIZE:-10}"

_BD_CACHE_DIR="${_BD_CACHE_DIR:-}"

# _bd_cache_get(type, key) -> cached value to stdout, or nothing
# Reads cached value from filesystem (Bash 3-compatible).
_bd_cache_get() {
    local type="$1" key="$2"
    [ -n "$_BD_CACHE_DIR" ] || return
    [ -f "$_BD_CACHE_DIR/${type}_${key}.txt" ] || return
    cat "$_BD_CACHE_DIR/${type}_${key}.txt"
}

# _bd_cache_set(type, key, val) -> no return
# Writes value to filesystem cache.
_bd_cache_set() {
    local type="$1" key="$2" val="$3"
    [ -n "$_BD_CACHE_DIR" ] || _BD_CACHE_DIR=$(mktemp -d 2>/dev/null || echo "")
    [ -n "$_BD_CACHE_DIR" ] || return
    printf '%s' "$val" > "$_BD_CACHE_DIR/${type}_${key}.txt"
}

# _bd_safe_key(str) -> sanitized key (alphanumeric, underscore, hyphen only)
# Sanitizes string for use as cache key.
_bd_safe_key() {
    echo "$1" | tr -c 'a-zA-Z0-9_-' '_'
}

# _get_user_display_name(login) -> display name to stdout (or empty)
# Fetches GitHub user display name via API; uses cache.
_get_user_display_name() {
    local login="$1"
    if [ -z "$login" ]; then
        echo ""
        return
    fi
    local key cached
    key=$(_bd_safe_key "$login")
    cached=$(_bd_cache_get "user" "$key")
    if [ -n "$cached" ]; then
        echo "$cached"
        return
    fi
    local name
    name=$(gh_api "users/$login" "{}" | jq -r '.name // ""' 2>/dev/null || echo "")
    _bd_cache_set "user" "$key" "$name"
    echo "$name"
}

# _is_org_member(login) -> "true" or "false" to stdout
# Checks if user is org member via API; uses cache.
_is_org_member() {
    local login="$1"
    if [ -z "$login" ]; then
        echo "false"
        return
    fi
    local key cached
    key=$(_bd_safe_key "$login")
    cached=$(_bd_cache_get "org" "$key")
    if [ -n "$cached" ]; then
        echo "$cached"
        return
    fi
    local result="false" api_result
    api_result=$(gh_api "orgs/${AT_OWNER}/members/$login" "null" || echo "null")
    if [ "$api_result" != "null" ]; then
        result="true"
    fi
    _bd_cache_set "org" "$key" "$result"
    echo "$result"
}

# _build_person_json(login, fallback_name) -> JSON object to stdout
# Builds person JSON with login, display name, is_org_member.
_build_person_json() {
    local login="$1" fallback_name="$2"
    local display_name="$fallback_name" org_member="false"

    if [ -n "$login" ]; then
        local fetched_name
        fetched_name=$(_get_user_display_name "$login")
        if [ -n "$fetched_name" ]; then
            display_name="$fetched_name"
        elif [ -z "$display_name" ]; then
            display_name="$login"
        fi
        org_member=$(_is_org_member "$login")
    else
        [ -n "$display_name" ] || display_name="(unknown)"
    fi

    jq -n \
        --arg login "$login" \
        --arg name "$display_name" \
        --arg org "$org_member" \
        '{login:$login, name:$name, is_org_member:($org == "true")}'
}

# _append_unique_person(arr_json, person_json) -> JSON array to stdout
# Appends person to array if not already present (by login or name).
_append_unique_person() {
    local arr_json="$1" person_json="$2"
    jq -n \
        --argjson arr "${arr_json:-[]}" \
        --argjson person "$person_json" \
        'if ($person.login // "") != "" then
            if any($arr[]?; .login == $person.login) then $arr else $arr + [$person] end
         else
            if any($arr[]?; (.login == "" and .name == $person.name)) then $arr else $arr + [$person] end
         end'
}

# _add_person_entry(login, fallback_name, current_json) -> JSON array to stdout
# Adds person to authors/approvers array, fetching details via API; avoids duplicates.
_add_person_entry() {
    local login="$1" fallback_name="$2" current_json="$3"
    local person_json
    person_json=$(_build_person_json "$login" "$fallback_name")
    if [ -n "$person_json" ]; then
        _append_unique_person "${current_json:-[]}" "$person_json"
    else
        echo "${current_json:-[]}"
    fi
}

# Download commit metadata for a batch of commits between start and end.
# batch_idx is zero-based. output_file receives appended JSON objects.
#
#   download_commit_batch "abc123" "def456" 0 "regression_analysis/data/commit_info.json"
#
download_commit_batch() {
    local start_commit="$1" end_commit="$2" batch_idx="$3" output_file="${4:-regression_analysis/data/commit_info.json}"

    if ! [[ "$batch_idx" =~ ^[0-9]+$ ]]; then
        echo "batch_downloader: batch_idx must be a non-negative integer" >&2
        return 1
    fi
    if ! git rev-parse --verify "$start_commit" >/dev/null 2>&1; then
        echo "batch_downloader: start commit '$start_commit' not found" >&2
        return 1
    fi
    if ! git rev-parse --verify "$end_commit" >/dev/null 2>&1; then
        echo "batch_downloader: end commit '$end_commit' not found" >&2
        return 1
    fi

    local commits commit_array total_commits
    commits=$(git log --format="%H" --first-parent "$start_commit".."$end_commit")
    echo "$commits" | grep -q "^$end_commit$" || commits="$commits"$'\n'"$end_commit" # add end_commit to the list if it's not already in the list
    commits=$(echo "$commits" | sort -u)
    commit_array=()
    while IFS= read -r line; do
        [ -n "$line" ] && commit_array+=("$line")
    done < <(echo "$commits" | awk 'NF') # split the commits into an array (this logic is somewhat confusing but it works)
    total_commits=${#commit_array[@]}

    if [ "$total_commits" -eq 0 ]; then
        return 0
    fi

    local start_offset end_offset slice_len
    start_offset=$((batch_idx * BATCH_SIZE))
    end_offset=$((start_offset + BATCH_SIZE))
    [ "$start_offset" -lt "$total_commits" ] || {
        echo "batch_downloader: batch index $batch_idx exceeds total commits ($total_commits)" >&2
        return 1
    }
    [ "$end_offset" -le "$total_commits" ] || end_offset="$total_commits"
    slice_len=$((end_offset - start_offset))

    selected_commits=()
    for ((i = start_offset; i < end_offset; i++)); do
        selected_commits+=("${commit_array[$i]}")
    done

    mkdir -p "$(dirname "$output_file")"
    [ -f "$output_file" ] || echo "[]" > "$output_file"

    local processed=0 skipped=0 errors=0 batch_count=${#selected_commits[@]} idx=0
    for commit_sha in "${selected_commits[@]}"; do
        [ -n "$commit_sha" ] || continue
        idx=$((idx + 1))

        local commit_short="${commit_sha:0:8}"
        local commit_msg pr_number
        commit_msg=$(git log -1 --format="%B" "$commit_sha" 2>/dev/null || echo "")
        pr_number=$(echo "$commit_msg" | grep -oE '\(#[0-9]+' | head -1 | sed 's/(#//' || echo "")

        if [ -z "$pr_number" ]; then
            skipped=$((skipped + 1))
            continue
        fi

        local pr_info reviews_json overview
        pr_info=$(get_pr_info "$pr_number")
        reviews_json=$(gh_api "repos/${AT_OWNER_REPO}/pulls/${pr_number}/reviews" "[]")

        overview="wasn't found"
        if [ "$reviews_json" != "[]" ] && [ -n "$reviews_json" ]; then
            local copilot_review
            copilot_review=$(echo "$reviews_json" | jq -r '.[] | select(.user.login == "copilot-pull-request-reviewer" or .user.login == "copilot-pull-request-reviewer[bot]") | .body' 2>/dev/null || echo "")
            if [ -n "$copilot_review" ]; then
                overview=$(echo "$copilot_review" | python3 - <<'PY'
import re
import sys
content = sys.stdin.read()
start = re.search(r'##\s+pull\s+request\s+overview', content, flags=re.IGNORECASE)
if start:
    section = content[start.end():]
    end = len(section)
    for pattern in (r'###\s+reviewed\s+changes', r'\n##\s+', r'\n---'):
        match = re.search(pattern, section, flags=re.IGNORECASE)
        if match:
            end = match.start()
            break
    snippet = section[:end].strip()
    print(snippet)
PY
) # extract the overview from the copilot review
                [ -n "$overview" ] || overview=$(echo "$copilot_review" | sed -n '/## [Pp]ull [Rr]equest [Oo]verview/,/### [Rr]eviewed [Cc]hanges/p' | sed '$d' | sed '1s/## [Pp]ull [Rr]equest [Oo]verview//' | sed 's/^[[:space:]]*//' | head -c 5000 || echo "")
                [ -n "$overview" ] || overview=$(echo "$copilot_review" | grep -i -A 50 "## Pull Request Overview" | tail -n +2 | head -n 30 | head -c 2000 || echo "") #fallbacks in case the python fails.
                [ -n "$overview" ] || overview="wasn't found"
            fi
        fi

        local commit_date commit_subject pr_title pr_url pr_description pr_author_login
        commit_date=$(git log -1 --format="%ai" "$commit_sha" 2>/dev/null || echo "")
        commit_subject=$(git log -1 --format="%s" "$commit_sha" 2>/dev/null || echo "")
        pr_title=$(echo "$pr_info" | jq -r '.title // ""' 2>/dev/null || echo "")
        pr_url=$(echo "$pr_info" | jq -r '.html_url // ""' 2>/dev/null || echo "")
        pr_description=$(echo "$pr_info" | jq -r '.body // ""' 2>/dev/null || echo "")
        pr_author_login=$(echo "$pr_info" | jq -r '.user.login // ""' 2>/dev/null || echo "")

        local commit_api commit_author_login commit_author_name co_author_names
        commit_api=$(get_commit_info "$commit_sha")
        commit_author_login=$(echo "$commit_api" | jq -r '.author.login // ""' 2>/dev/null || echo "")
        commit_author_name=$(echo "$commit_api" | jq -r '.commit.author.name // ""' 2>/dev/null || echo "")
        co_author_names=$(git log -1 --format="%B" "$commit_sha" 2>/dev/null |
            awk '/^Co-authored-by:/ { sub(/^Co-authored-by:[[:space:]]*/, ""); sub(/<.*>/, ""); gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print }' |
            jq -R -s -c 'split("\n") | map(select(length > 0))' 2>/dev/null)
        [ -n "$co_author_names" ] || co_author_names="[]"

        local authors_json='[]'
        authors_json=$(_add_person_entry "$pr_author_login" "" "$authors_json")
        authors_json=$(_add_person_entry "$commit_author_login" "$commit_author_name" "$authors_json")
        if [ "$co_author_names" != "[]" ]; then
            while IFS= read -r co_name; do
                [ -n "$co_name" ] || continue
                authors_json=$(_add_person_entry "" "$co_name" "$authors_json")
            done < <(echo "$co_author_names" | jq -r '.[]')
        fi

        local approvers_json='[]'
        if [ "$reviews_json" != "[]" ] && [ -n "$reviews_json" ]; then
            while IFS= read -r approver_login; do
                [ -n "$approver_login" ] || continue
                approvers_json=$(_add_person_entry "$approver_login" "" "$approvers_json")
            done < <(echo "$reviews_json" | jq -r '.[] | select(.state=="APPROVED") | .user.login | select(length > 0)' | sort -u)
        fi

        local entry
        entry=$(jq -n \
            --arg commit "$commit_sha" \
            --arg commit_short "$commit_short" \
            --arg commit_date "$commit_date" \
            --arg commit_subject "$commit_subject" \
            --arg pr_number "$pr_number" \
            --arg pr_title "$pr_title" \
            --arg pr_url "$pr_url" \
            --arg pr_description "$pr_description" \
            --argjson authors "$authors_json" \
            --argjson approvers "${approvers_json:-[]}" \
            --arg overview "$overview" \
            '{commit: $commit, commit_short: $commit_short, commit_date: $commit_date, commit_subject: $commit_subject, pr_number: $pr_number, pr_title: $pr_title, pr_url: $pr_url, pr_description: $pr_description, authors: $authors, approvers: $approvers, copilot_overview: $overview}' 2>/dev/null || echo "{}")

        if [ "$entry" != "{}" ]; then
            if jq ". += [$entry]" "$output_file" > "${output_file}.tmp" 2>/dev/null && mv "${output_file}.tmp" "$output_file"; then
                processed=$((processed + 1))
            else
                errors=$((errors + 1))
            fi
        else
            errors=$((errors + 1))
        fi
    done

    echo "batch_downloader: processed=$processed skipped=$skipped errors=$errors"
}

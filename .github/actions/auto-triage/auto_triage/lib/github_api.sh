#!/bin/bash
#
# github_api.sh - GitHub API wrapper functions for auto-triage
#
# Provides thin, consistent wrappers around `gh api` with:
#   - Silent stderr by default (no noisy 404s in logs)
#   - Configurable JSON fallback on failure
#   - POST support with HTTP status inspection
#   - Common endpoint helpers (workflows, runs, jobs, commits, PRs)
#
# Prerequisites: gh CLI authenticated.
# Usage: source this file (it pulls in config.sh, which pulls in common.sh).
#

if [ -n "${_AUTO_TRIAGE_GITHUB_API_LOADED:-}" ]; then
    return 0
fi
_AUTO_TRIAGE_GITHUB_API_LOADED=1

# github_api.sh depends on config.sh (AT_OWNER_REPO, AT_PER_PAGE, etc.).
_GITHUB_API_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "$_GITHUB_API_LIB_DIR/config.sh"

# ==============================================================================
# Low-level helpers
# ==============================================================================

# GET a GitHub API endpoint.  Returns the JSON body on success, or $fallback
# (default "{}") on any error.
#
#   gh_api "repos/owner/repo/actions/runs/123" [fallback]
#
gh_api() {
    local endpoint="$1"
    local fallback="${2:-{\}}"
    local result
    if result=$(gh api "$endpoint" 2>/dev/null); then
        echo "$result"
    else
        echo "$fallback"
    fi
}

# GET with a jq selector applied server-side (--jq).
#
#   gh_api_jq "repos/o/r/commits/SHA" '.author.login // empty' ""
#
gh_api_jq() {
    local endpoint="$1" jq_expr="$2" fallback="${3:-}"
    gh api "$endpoint" --jq "$jq_expr" 2>/dev/null || echo "$fallback"
}

# POST to a GitHub API endpoint.  Prints the full response (headers + body,
# via -i) so callers can inspect the HTTP status code.  Returns "API_ERROR"
# on complete failure.
#
#   response=$(gh_api_post "repos/o/r/actions/jobs/123/rerun")
#   status=$(echo "$response" | head -1 | awk '{print $2}')
#
gh_api_post() {
    local endpoint="$1"
    gh api --method POST "$endpoint" -i 2>&1 || echo "API_ERROR"
}

# ==============================================================================
# Workflow helpers
# ==============================================================================

# Resolve a workflow filename (e.g. "ci.yml") to its numeric ID.
# Tries both .yml and .yaml extensions.  Prints the ID or "" on failure.
#
#   wf_id=$(get_workflow_id "ci.yml")
#
get_workflow_id() {
    local name="$1"
    local raw id

    # Try the name as given
    raw=$(gh_api "repos/${AT_OWNER_REPO}/actions/workflows/${name}" "")
    id=$(echo "$raw" | jq -r '.id // empty' 2>/dev/null)
    if [ -n "$id" ]; then echo "$id"; return 0; fi

    # Try swapping .yml <-> .yaml
    local alt
    case "$name" in
        *.yml)  alt="${name%.yml}.yaml" ;;
        *.yaml) alt="${name%.yaml}.yml" ;;
        *)      return 1 ;;
    esac
    raw=$(gh_api "repos/${AT_OWNER_REPO}/actions/workflows/${alt}" "")
    id=$(echo "$raw" | jq -r '.id // empty' 2>/dev/null)
    if [ -n "$id" ]; then echo "$id"; return 0; fi

    return 1
}

# Fetch one page of workflow runs.
#
#   page_json=$(get_workflow_runs "$wf_id" 1)
#
get_workflow_runs() {
    local wf_id="$1" page="${2:-1}"
    gh_api "repos/${AT_OWNER_REPO}/actions/workflows/${wf_id}/runs?branch=main&per_page=${AT_PER_PAGE}&page=${page}"
}

# ==============================================================================
# Job / run helpers
# ==============================================================================

# Get jobs for a run (optionally a specific attempt).
#
#   jobs_json=$(get_jobs_for_run "$run_id")
#   jobs_json=$(get_jobs_for_run "$run_id" 3)
#
get_jobs_for_run() {
    local run_id="$1" attempt="${2:-}"
    if [ -n "$attempt" ]; then
        gh_api "repos/${AT_OWNER_REPO}/actions/runs/${run_id}/attempts/${attempt}/jobs?per_page=${AT_PER_PAGE}"
    else
        gh_api "repos/${AT_OWNER_REPO}/actions/runs/${run_id}/jobs?per_page=${AT_PER_PAGE}"
    fi
}

# Get metadata for a single job by ID.
#
#   job_json=$(get_job_info "$job_id")
#
get_job_info() {
    gh_api "repos/${AT_OWNER_REPO}/actions/jobs/$1"
}

# Get metadata for a workflow run.
#
#   run_json=$(get_run_info "$run_id")
#
get_run_info() {
    gh_api "repos/${AT_OWNER_REPO}/actions/runs/$1"
}

# ==============================================================================
# Commit / PR helpers
# ==============================================================================

# Get commit metadata (author, message, etc.).
#
#   commit_json=$(get_commit_info "abc123")
#
get_commit_info() {
    gh_api "repos/${AT_OWNER_REPO}/commits/$1"
}

# Get the author login for a commit, or "" if unavailable.
#
#   author=$(get_commit_author "abc123")
#
get_commit_author() {
    gh_api_jq "repos/${AT_OWNER_REPO}/commits/$1" '.author.login // empty' ""
}

# Find the PR number associated with a commit, or "" if none.
#
#   pr_num=$(get_pr_for_commit "abc123")
#
get_pr_for_commit() {
    gh api -H "Accept: application/vnd.github.groot-preview+json" \
        "repos/${AT_OWNER_REPO}/commits/$1/pulls" \
        --jq '.[0].number // empty' 2>/dev/null || echo ""
}

# Get approved reviewers for a PR (unique logins, one per line).
#
#   reviewers=$(get_pr_approvers 42)
#
get_pr_approvers() {
    local pr_number="$1"
    gh api "repos/${AT_OWNER_REPO}/pulls/${pr_number}/reviews" \
        --jq '[.[] | select(.state=="APPROVED") | .user.login] | unique[]' \
        2>/dev/null || echo ""
}

# Get full PR metadata.
#
#   pr_json=$(get_pr_info 42)
#
get_pr_info() {
    gh_api "repos/${AT_OWNER_REPO}/pulls/$1"
}

# ==============================================================================
# Annotation helpers
# ==============================================================================

# Fetch check-run annotations (paginated).  Returns a JSON array.
#
#   annotations=$(get_check_annotations "$check_run_id")
#
get_check_annotations() {
    local check_id="$1"
    local page=1 all="[]" batch

    while true; do
        batch=$(gh_api "repos/${AT_OWNER_REPO}/check-runs/${check_id}/annotations?per_page=100&page=${page}" "[]")
        local count
        count=$(echo "$batch" | jq 'length' 2>/dev/null || echo 0)
        [ "$count" -gt 0 ] || break
        all=$(echo "$all" "$batch" | jq -s '.[0] + .[1]' 2>/dev/null || echo "$all")
        page=$((page + 1))
    done
    echo "$all"
}

# ==============================================================================
# Log download helper
# ==============================================================================

# Download the full log zip for a workflow run into a target directory.
# Extracts the zip and removes the temporary file.
# Uses AT_OWNER_REPO from config (same as other helpers in this file).
#
#   download_run_logs "$run_id" "/tmp/logs"
#
download_run_logs() {
    local run_id="$1" dest="$2"
    local tmp_zip
    tmp_zip="$(mktemp --suffix=.zip 2>/dev/null || mktemp)"
    mkdir -p "$dest"
    gh api "repos/${AT_OWNER_REPO}/actions/runs/${run_id}/logs" > "$tmp_zip" 2>/dev/null || {
        rm -f "$tmp_zip"
        return 1
    }
    unzip -oq "$tmp_zip" -d "$dest" 2>/dev/null
    rm -f "$tmp_zip"
}

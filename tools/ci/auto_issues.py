#!/usr/bin/env python3
"""Create GitHub issues for CI jobs that fail deterministically (3 runs in a row).

Downloads workflow data from the aggregate-workflow-data artifact, identifies
jobs that failed in the last N consecutive runs, checks for existing issues,
creates new ones, resolves likely owners, and posts Slack notifications.

All configuration is via environment variables (see below).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

TARGET_REPO = os.environ.get("TARGET_REPO", "tenstorrent/tt-metal")
ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
CREATE_ISSUES = os.environ.get("CREATE_ISSUES", "false").lower() == "true"
CONSECUTIVE_FAILURES = int(os.environ.get("CONSECUTIVE_FAILURES", "3"))
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
OWNERS_JSON_PATH = Path(os.environ.get("OWNERS_JSON_PATH", "tt-metal/.github/actions/analyze-workflow-data/owners.json"))
PIPELINE_REORG_DIR = Path(os.environ.get("PIPELINE_REORG_DIR", "tt-metal/tests/pipeline_reorg"))
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN") or os.environ.get("GITHUB_TOKEN", "")

SKIP_KEYWORDS: tuple[str, ...] = ("sanity",)
WORKFLOW_FILE = "aggregate-workflow-data.yaml"
ARTIFACT_NAME = "workflow-data"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def gh(*args: str, token: str | None = None, timeout: int = 30) -> str:
    env = {**os.environ}
    if token:
        env["GITHUB_TOKEN"] = token
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:4])}... failed: {proc.stderr.strip()}")
    return proc.stdout


def api_get(url: str, token: str | None = None, retries: int = 3) -> Any:
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"API request failed: {url}: {exc}") from exc
    raise RuntimeError(f"Exhausted retries for {url}")


def slack_post(channel: str, text: str) -> dict[str, Any]:
    data = urllib.parse.urlencode({"channel": channel, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    if not payload.get("ok"):
        raise RuntimeError(f"Slack error: {payload.get('error', 'unknown')}")
    return payload


# ---------------------------------------------------------------------------
# Step 1: Download workflow data
# ---------------------------------------------------------------------------


def download_workflow_data() -> list[list]:
    log("Finding latest aggregate-workflow-data run...")
    raw = gh(
        "run", "list",
        f"--workflow={WORKFLOW_FILE}", f"--repo={TARGET_REPO}",
        "--status=completed", "--limit=1",
        "--json=databaseId,conclusion",
    )
    runs = json.loads(raw)
    if not runs:
        raise RuntimeError("No completed aggregate-workflow-data runs found")
    run_id = runs[0]["databaseId"]
    log(f"  Latest run: {run_id} ({runs[0]['conclusion']})")

    with tempfile.TemporaryDirectory() as tmpdir:
        gh("run", "download", str(run_id),
           f"--repo={TARGET_REPO}", "-n", ARTIFACT_NAME, "-D", tmpdir,
           timeout=120)
        json_file = Path(tmpdir) / "workflow-data.json"
        if not json_file.exists():
            candidates = list(Path(tmpdir).rglob("*.json"))
            if not candidates:
                raise RuntimeError("No JSON files in downloaded artifact")
            json_file = candidates[0]
        log(f"  Loaded workflow data ({json_file.stat().st_size / 1_000_000:.1f} MB)")
        return json.loads(json_file.read_text())


# ---------------------------------------------------------------------------
# Step 2: Find 3-consecutive-failure jobs
# ---------------------------------------------------------------------------


def find_failing_jobs(workflow_data: list[list]) -> list[dict[str, Any]]:
    token = os.environ.get("GITHUB_TOKEN")
    owner, repo = TARGET_REPO.split("/")
    results: list[dict[str, Any]] = []

    for workflow_name, runs in workflow_data:
        if not runs:
            continue
        if any(kw.lower() in str(workflow_name).lower() for kw in SKIP_KEYWORDS):
            log(f"  Skipping '{workflow_name}' (skip keyword)")
            continue

        sorted_runs = sorted(
            runs,
            key=lambda r: r.get("created_at", "") or r.get("run_started_at", ""),
            reverse=True,
        )[:CONSECUTIVE_FAILURES]

        if len(sorted_runs) < CONSECUTIVE_FAILURES:
            continue
        if not all(r.get("conclusion") == "failure" for r in sorted_runs):
            continue

        log(f"  '{workflow_name}': {CONSECUTIVE_FAILURES} consecutive failures, fetching jobs...")
        run_failed_jobs: dict[int, dict[str, str]] = {}
        for run in sorted_runs:
            run_id = run.get("id")
            if not run_id:
                continue
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100"
            data = api_get(url, token)
            failed = {
                j["name"]: j.get("html_url", "")
                for j in data.get("jobs", [])
                if j.get("conclusion") == "failure"
            }
            run_failed_jobs[run_id] = failed
            time.sleep(0.3)

        if len(run_failed_jobs) < CONSECUTIVE_FAILURES:
            continue

        all_run_ids = list(run_failed_jobs.keys())
        common_jobs = set(run_failed_jobs[all_run_ids[0]])
        for rid in all_run_ids[1:]:
            common_jobs &= set(run_failed_jobs[rid])

        for job_name in sorted(common_jobs):
            job_urls = [run_failed_jobs[rid].get(job_name, "") for rid in all_run_ids]
            run_urls = [r.get("html_url", "") for r in sorted_runs]
            results.append({
                "workflow_name": workflow_name,
                "job_name": job_name,
                "job_urls": job_urls,
                "run_urls": run_urls,
            })

    log(f"  Found {len(results)} deterministically-failing jobs")
    return results


# ---------------------------------------------------------------------------
# Step 3: Check existing issues
# ---------------------------------------------------------------------------


def job_identity_key(workflow_name: str, job_name: str) -> str:
    raw = f"{workflow_name}\n{job_name}".strip().lower().encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def load_open_issues() -> list[dict[str, Any]]:
    raw = gh(
        "issue", "list",
        f"--repo={ISSUE_REPO}", "--state=open", "--limit=200",
        "--json=number,title,body,url",
        token=ISSUE_WRITE_TOKEN,
    )
    return json.loads(raw)


def has_existing_issue(job: dict[str, Any], open_issues: list[dict[str, Any]]) -> str | None:
    key = job_identity_key(job["workflow_name"], job["job_name"])
    marker = f"Auto-triage-job-key: {key}"
    for issue in open_issues:
        body = issue.get("body") or ""
        if marker in body:
            return issue.get("url", f"#{issue['number']}")
    return None


# ---------------------------------------------------------------------------
# Step 4: Resolve owners
# ---------------------------------------------------------------------------


def load_owners_json() -> list[dict[str, Any]]:
    if not OWNERS_JSON_PATH.exists():
        log(f"  Warning: {OWNERS_JSON_PATH} not found")
        return []
    data = json.loads(OWNERS_JSON_PATH.read_text())
    return data.get("contains", [])


def load_pipeline_reorg_owners() -> list[dict[str, Any]]:
    """Parse pipeline_reorg YAMLs for name + owner_id entries.

    Uses simple regex parsing to avoid a PyYAML dependency.
    """
    entries: list[dict[str, Any]] = []
    if not PIPELINE_REORG_DIR.exists():
        log(f"  Warning: {PIPELINE_REORG_DIR} not found")
        return entries
    for yaml_file in sorted(PIPELINE_REORG_DIR.glob("*.yaml")):
        text = yaml_file.read_text()
        current_name: str | None = None
        for line in text.splitlines():
            name_match = re.match(r'^- name:\s*"(.+)"', line)
            if name_match:
                current_name = name_match.group(1)
                continue
            owner_match = re.match(r'^\s+owner_id:\s*(.+)', line)
            if owner_match and current_name:
                rest = owner_match.group(1).strip()
                raw_id = rest.split("#")[0].strip().split()[0]
                name = rest.split("#", 1)[1].strip() if "#" in rest else ""
                entries.append({"name": current_name, "id": raw_id, "owner_name": name})
                current_name = None
    return entries


def resolve_owners(workflow_name: str, job_name: str) -> list[dict[str, str]]:
    combined = f"{workflow_name} / {job_name}".lower()
    job_lower = job_name.lower()

    # Pipeline reorg takes precedence
    for entry in _pipeline_owners:
        entry_name = entry["name"].lower()
        if entry_name in job_lower or job_lower in entry_name:
            return [{"id": entry["id"], "name": entry.get("owner_name", "")}]

    # Fall back to owners.json
    for rec in _owners_json:
        component = str(rec.get("job-name-component", "")).lower()
        if not component:
            continue
        if component in combined or combined in component:
            owner = rec.get("owner")
            if isinstance(owner, list):
                return [{"id": o["id"], "name": o.get("name", "")} for o in owner]
            if isinstance(owner, dict):
                return [{"id": owner["id"], "name": owner.get("name", "")}]
    return []


# ---------------------------------------------------------------------------
# Step 5: Create issues
# ---------------------------------------------------------------------------


def create_issue(job: dict[str, Any]) -> str:
    key = job_identity_key(job["workflow_name"], job["job_name"])
    run_links = "\n".join(f"- {url}" for url in job["run_urls"] if url)
    job_links = "\n".join(f"- {url}" for url in job["job_urls"] if url)
    title = f"[CI] {job['workflow_name']} / {job['job_name']} -- deterministic failure"
    body = f"""## Deterministic CI Failure

**Workflow:** `{job['workflow_name']}`
**Job:** `{job['job_name']}`
**Consecutive failures:** {CONSECUTIVE_FAILURES}

### Failing run links
{run_links}

### Failing job links
{job_links}

---
_Auto-created by CI triage. Do not remove the marker below._
`Auto-triage-job-key: {key}`
"""
    raw = gh(
        "issue", "create",
        f"--repo={ISSUE_REPO}",
        f"--title={title}",
        f"--body={body}",
        "--label=CI auto triage",
        token=ISSUE_WRITE_TOKEN,
    )
    issue_url = raw.strip()
    log(f"  Created issue: {issue_url}")
    return issue_url


# ---------------------------------------------------------------------------
# Step 6: Send Slack message
# ---------------------------------------------------------------------------


def send_slack_notification(job: dict[str, Any], issue_url: str, owners: list[dict[str, str]]) -> None:
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        log("  Skipping Slack notification (no token or channel)")
        return

    owner_text = ", ".join(f"<@{o['id']}>" for o in owners) if owners else "No owners identified -- please triage manually"
    run_links = ", ".join(f"<{url}|run>" for url in job["run_urls"][:3] if url)

    text = (
        f":rotating_light: *New CI auto-triage issue created*\n"
        f"*Job:* `{job['workflow_name']} / {job['job_name']}`\n"
        f"*Issue:* {issue_url}\n"
        f"*Failed {CONSECUTIVE_FAILURES} runs in a row:* {run_links}\n"
        f"*Likely owners:* {owner_text}"
    )

    slack_post(SLACK_CHANNEL_ID, text)
    log(f"  Sent Slack notification for {job['job_name']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_owners_json: list[dict[str, Any]] = []
_pipeline_owners: list[dict[str, Any]] = []


def main() -> int:
    global _owners_json, _pipeline_owners

    log("=== Auto Issue Creation ===")
    log(f"Target repo: {TARGET_REPO}")
    log(f"Issue repo:  {ISSUE_REPO}")
    log(f"Create issues: {CREATE_ISSUES}")

    workflow_data = download_workflow_data()
    failing_jobs = find_failing_jobs(workflow_data)

    if not failing_jobs:
        log("No deterministic failures found. Done.")
        print(json.dumps({"created": 0, "skipped": 0, "failures": []}))
        return 0

    open_issues = load_open_issues()
    log(f"Loaded {len(open_issues)} open issues from {ISSUE_REPO}")

    _owners_json = load_owners_json()
    _pipeline_owners = load_pipeline_reorg_owners()
    log(f"Loaded {len(_owners_json)} owners.json entries, {len(_pipeline_owners)} pipeline_reorg entries")

    created = 0
    skipped = 0
    summary: list[dict[str, Any]] = []

    for job in failing_jobs:
        existing = has_existing_issue(job, open_issues)
        if existing:
            log(f"  Already tracked: {job['job_name']} -> {existing}")
            skipped += 1
            summary.append({"job": job["job_name"], "action": "skipped", "existing": existing})
            continue

        owners = resolve_owners(job["workflow_name"], job["job_name"])
        owner_names = [o.get("name") or o["id"] for o in owners]
        log(f"  New failure: {job['job_name']} (owners: {owner_names})")

        if not CREATE_ISSUES:
            log("  Dry run -- would create issue")
            summary.append({"job": job["job_name"], "action": "dry_run", "owners": owner_names})
            continue

        issue_url = create_issue(job)
        send_slack_notification(job, issue_url, owners)
        created += 1
        summary.append({"job": job["job_name"], "action": "created", "issue": issue_url, "owners": owner_names})

    log(f"\nDone: {created} created, {skipped} skipped, {len(failing_jobs)} total failures")
    print(json.dumps({"created": created, "skipped": skipped, "failures": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

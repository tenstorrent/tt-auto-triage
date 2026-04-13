#!/usr/bin/env python3
"""Create a draft PR for a CI triage issue.

Given an issue from the triage issue repo, this script:
1. Fetches the issue details
2. Checks if a draft PR already exists
3. Uses the Cursor CLI agent to either fix or disable the failing test
4. Commits, pushes, and creates a draft PR
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = SCRIPT_DIR / "prompts"
PR_MARKER_PREFIX = "Auto-triage-issue:"


def log(msg: str) -> None:
    print(f"[draft-pr] {msg}", flush=True)


def run(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc_env = None
    if env:
        proc_env = os.environ.copy()
        proc_env.update(env)
    proc = subprocess.run(
        cmd, text=True, capture_output=capture, env=proc_env, cwd=cwd, check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(shlex.quote(x) for x in cmd)}\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )
    return proc


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def github_login_for_token(token: str) -> tuple[str | None, str]:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "tt-auto-triage-draft-pr",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except Exception as exc:
        return None, str(exc)
    login = payload.get("login", "")
    return (login, "ok") if login else (None, "empty_login")


def fetch_issue(issue_repo: str, issue_number: int, token: str) -> dict:
    log(f"Fetching issue #{issue_number} from {issue_repo}")
    proc = run(
        [
            "gh", "issue", "view",
            "--repo", issue_repo,
            str(issue_number),
            "--json", "title,body,labels,url,state",
        ],
        env={"GH_TOKEN": token},
    )
    return json.loads(proc.stdout)


def find_existing_pr(target_pr_repo: str, issue_number: int, token: str) -> dict | None:
    """Search for an open PR that already addresses this issue."""
    log(f"Checking for existing PR for issue #{issue_number} in {target_pr_repo}")
    proc = run(
        [
            "gh", "pr", "list",
            "--repo", target_pr_repo,
            "--state", "open",
            "--json", "number,url,body,title",
            "--limit", "100",
        ],
        env={"GH_TOKEN": token},
    )
    prs = json.loads(proc.stdout)
    marker = f"{PR_MARKER_PREFIX} {issue_number}"
    for pr in prs:
        body = pr.get("body", "") or ""
        if marker in body:
            return pr
    return None


def create_draft_pr(
    *,
    target_pr_repo: str,
    target_pr_base: str,
    branch: str,
    title: str,
    body: str,
    labels: list[str],
    token: str,
    cwd: str | Path,
) -> dict:
    log(f"Creating draft PR on {target_pr_repo}")
    cmd = [
        "gh", "pr", "create",
        "--repo", target_pr_repo,
        "--base", target_pr_base,
        "--head", branch,
        "--title", title,
        "--body", body,
        "--draft",
    ]
    for label in labels:
        cmd.extend(["--label", label])
    proc = run(cmd, env={"GH_TOKEN": token}, cwd=cwd)
    url = proc.stdout.strip()
    return {"url": url}


def add_issue_label(issue_repo: str, issue_number: int, label: str, token: str) -> None:
    log(f"Adding label '{label}' to issue #{issue_number}")
    run(
        [
            "gh", "issue", "edit",
            "--repo", issue_repo,
            str(issue_number),
            "--add-label", label,
        ],
        env={"GH_TOKEN": token},
        check=False,
    )


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def branch_exists_remote(branch: str, repo_slug: str) -> bool:
    check = run(
        ["git", "ls-remote", "--heads", f"https://github.com/{repo_slug}.git", f"refs/heads/{branch}"],
        check=False, capture=True,
    )
    return bool((check.stdout or "").strip())


def choose_branch_name(base: str, repo_slug: str) -> str:
    if not branch_exists_remote(base, repo_slug):
        return base
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{base}-{ts}"


def push_branch(branch: str, target_pr_repo: str, token: str, cwd: str | Path) -> None:
    login, reason = github_login_for_token(token)
    if not login:
        raise RuntimeError(f"Cannot resolve token login for git push: {reason}")

    push_url = f"https://github.com/{target_pr_repo}.git"
    askpass_script = Path(tempfile.mkdtemp(prefix="git-askpass-")) / "askpass.sh"
    askpass_script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  *Username*) echo "$GIT_PUSH_USER" ;;\n'
        '  *) echo "$GIT_PUSH_TOKEN" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    askpass_script.chmod(0o700)

    log(f"Pushing branch {branch} to {target_pr_repo}")
    run(
        ["git", "push", "-u", push_url, f"HEAD:refs/heads/{branch}"],
        cwd=cwd,
        capture=True,
        env={
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(askpass_script),
            "GIT_PUSH_USER": login,
            "GIT_PUSH_TOKEN": token,
        },
    )


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------

def render_prompt(template_path: Path, replacements: dict[str, str]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def run_agent(prompt: str, model: str, cwd: str | Path) -> subprocess.CompletedProcess[str]:
    cmd = ["cursor", "--trust", "-p", prompt]
    if model and model != "auto":
        cmd[1:1] = ["--model", model]
    log(f"Running Cursor agent (model={model}) in {cwd}")
    return run(cmd, cwd=cwd, check=False, capture=True)


def has_changes(cwd: str | Path) -> bool:
    proc = run(["git", "status", "--porcelain"], cwd=cwd, capture=True)
    return bool(proc.stdout.strip())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a draft PR for a CI triage issue.")
    p.add_argument("--issue-number", required=True, type=int)
    p.add_argument("--issue-repo", default="ebanerjeeTT/issue_dump")
    p.add_argument("--target-pr-repo", default="ebanerjeeTT/tt-metal")
    p.add_argument("--target-pr-base", default="main")
    p.add_argument("--try-to-fix", action="store_true")
    p.add_argument("--model", default="claude-4-sonnet")
    p.add_argument("--work-dir", required=True, help="Path to tt-metal checkout")
    p.add_argument("--output-json", required=True)
    p.add_argument("--summary-md", required=True)
    return p.parse_args()


def write_result(path: str, data: dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_summary(path: str, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def main() -> int:
    args = parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        log("ERROR: GITHUB_TOKEN environment variable is required")
        return 2

    cursor_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not cursor_key:
        log("ERROR: CURSOR_API_KEY environment variable is required")
        return 2

    work_dir = Path(args.work_dir).resolve()
    if not work_dir.is_dir():
        log(f"ERROR: work-dir does not exist: {work_dir}")
        return 2

    mode = "fix" if args.try_to_fix else "disable"
    log(f"Mode: {mode} | Issue: #{args.issue_number} | Repo: {args.issue_repo}")

    # 1. Fetch issue
    try:
        issue = fetch_issue(args.issue_repo, args.issue_number, token)
    except Exception as exc:
        log(f"ERROR: Failed to fetch issue: {exc}")
        write_result(args.output_json, {"status": "error", "reason": str(exc)})
        write_summary(args.summary_md, f"## Draft PR Creation\n\nFailed to fetch issue #{args.issue_number}: {exc}")
        return 1

    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "")
    issue_url = issue.get("url", "")
    log(f"Issue title: {issue_title}")

    # 2. Check for existing PR
    existing = find_existing_pr(args.target_pr_repo, args.issue_number, token)
    if existing:
        pr_url = existing.get("url", "")
        log(f"PR already exists: {pr_url}")
        write_result(args.output_json, {
            "status": "skipped",
            "reason": "pr_already_exists",
            "existing_pr_url": pr_url,
        })
        write_summary(args.summary_md, textwrap.dedent(f"""\
            ## Draft PR Creation

            **Status:** Skipped (PR already exists)
            **Issue:** #{args.issue_number} - {issue_title}
            **Existing PR:** {pr_url}
        """))
        return 0

    # 3. Create branch
    base_branch = f"auto-triage/issue-{args.issue_number}-{mode}"
    branch = choose_branch_name(base_branch, args.target_pr_repo)
    log(f"Branch: {branch}")

    run(["git", "config", "user.name", "CI Auto Triage Bot"], cwd=work_dir)
    run(["git", "config", "user.email", "ci-auto-triage-bot@tenstorrent.invalid"], cwd=work_dir)
    run(["git", "checkout", "-B", branch], cwd=work_dir)

    # 4. Build prompt
    prompt_file = PROMPTS_DIR / (f"draft_pr_{mode}.txt")
    if not prompt_file.exists():
        log(f"ERROR: Prompt template not found: {prompt_file}")
        return 2

    prompt = render_prompt(prompt_file, {
        "__ISSUE_TITLE__": issue_title,
        "__ISSUE_BODY__": issue_body,
        "__ISSUE_URL__": issue_url,
        "__ISSUE_NUMBER__": str(args.issue_number),
    })

    # 5. Run Cursor agent
    agent_result = run_agent(prompt, args.model, work_dir)
    log(f"Agent exit code: {agent_result.returncode}")
    if agent_result.stdout:
        agent_stdout_tail = agent_result.stdout[-2000:]
        log(f"Agent output (tail):\n{agent_stdout_tail}")
    if agent_result.stderr:
        log(f"Agent stderr (tail):\n{agent_result.stderr[-1000:]}")

    # 6. Check for changes
    if not has_changes(work_dir):
        log("Agent made no changes")
        write_result(args.output_json, {
            "status": "no_changes",
            "reason": "agent_made_no_changes",
            "agent_exit_code": agent_result.returncode,
        })
        write_summary(args.summary_md, textwrap.dedent(f"""\
            ## Draft PR Creation

            **Status:** Failed (no changes produced)
            **Issue:** #{args.issue_number} - {issue_title}
            **Mode:** {mode}
            **Agent exit code:** {agent_result.returncode}
        """))
        return 1

    # 7. Commit
    diff_stat = run(["git", "diff", "--stat"], cwd=work_dir, capture=True)
    log(f"Changes:\n{diff_stat.stdout}")

    commit_prefix = "fix" if mode == "fix" else "ci"
    commit_msg = f"{commit_prefix}: auto-triage {mode} for issue #{args.issue_number}\n\n{issue_title}\n\nSee: {issue_url}"
    run(["git", "add", "-A"], cwd=work_dir)
    run(["git", "commit", "-m", commit_msg], cwd=work_dir)

    # 8. Push
    try:
        push_branch(branch, args.target_pr_repo, token, work_dir)
    except Exception as exc:
        log(f"ERROR: Push failed: {exc}")
        write_result(args.output_json, {"status": "error", "reason": f"push_failed: {exc}"})
        write_summary(args.summary_md, f"## Draft PR Creation\n\nPush failed: {exc}")
        return 1

    # 9. Create draft PR
    pr_title = f"{'fix' if mode == 'fix' else 'ci'}: auto-triage {mode} for issue #{args.issue_number}"
    pr_body = textwrap.dedent(f"""\
        ## Auto-Triage Draft PR

        **Mode:** {mode}
        **Source issue:** {args.issue_repo}#{args.issue_number}
        **Issue title:** {issue_title}

        ---

        {commit_msg}

        ---

        <!-- {PR_MARKER_PREFIX} {args.issue_number} -->
    """)

    labels = ["auto-triage", f"auto-triage:{mode}"]
    try:
        pr_info = create_draft_pr(
            target_pr_repo=args.target_pr_repo,
            target_pr_base=args.target_pr_base,
            branch=branch,
            title=pr_title,
            body=pr_body,
            labels=labels,
            token=token,
            cwd=work_dir,
        )
    except Exception as exc:
        log(f"ERROR: PR creation failed: {exc}")
        write_result(args.output_json, {"status": "error", "reason": f"pr_create_failed: {exc}"})
        write_summary(args.summary_md, f"## Draft PR Creation\n\nPR creation failed: {exc}")
        return 1

    pr_url = pr_info.get("url", "")
    log(f"Draft PR created: {pr_url}")

    # 10. Label the issue
    add_issue_label(args.issue_repo, args.issue_number, "auto-triage:pr-created", token)

    # Write outputs
    write_result(args.output_json, {
        "status": "created",
        "pr_url": pr_url,
        "branch": branch,
        "mode": mode,
        "issue_number": args.issue_number,
        "issue_title": issue_title,
        "files_changed": diff_stat.stdout.strip(),
    })
    write_summary(args.summary_md, textwrap.dedent(f"""\
        ## Draft PR Creation

        **Status:** Created
        **Issue:** #{args.issue_number} - {issue_title}
        **Mode:** {mode}
        **Draft PR:** {pr_url}
        **Branch:** `{branch}`

        ### Changes
        ```
        {diff_stat.stdout.strip()}
        ```
    """))

    log("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

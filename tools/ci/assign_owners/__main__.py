#!/usr/bin/env python3
# Hybrid owner resolver: pipeline_reorg fast path, Cursor agent slow path.
from __future__ import annotations

import json
import os
import re
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from .github import (
    add_issue_labels,
    create_issue_comment,
    download_slack_directory,
    list_issue_comments,
    list_open_issues,
    log,
    update_issue,
    update_issue_comment,
)
from .identity import build_identity_index
from .issue_state import has_assignee_markers, parse_base_markers, strip_assignee_markers

ISSUE_REPO = os.environ.get("ISSUE_REPO", "ebanerjeeTT/issue_dump")
ISSUE_WRITE_TOKEN = os.environ.get("ISSUE_WRITE_TOKEN", "")
AGGREGATE_READ_TOKEN = os.environ.get("AGGREGATE_READ_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
PIPELINE_REORG_DIR = Path(os.environ.get("PIPELINE_REORG_DIR", "tt-metal/tests/pipeline_reorg"))
TARGET_REPO_ROOT = Path(os.environ.get("TARGET_REPO_ROOT", "tt-metal"))
SLACK_DUMP_PATH = Path(os.environ.get("SLACK_DUMP_PATH", "slack_users.json"))
SUMMARY_OUTPUT = os.environ.get("SUMMARY_OUTPUT", "")
CURSOR_MODEL = os.environ.get("CURSOR_MODEL", "claude-4-sonnet")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "tenstorrent")
OWNERS_READY_LABEL = "auto-triage:owners-ready"
AGENT_MARKER = "===FINAL==="
COMMENT_MARKER = "<!-- auto-triage:owner-recommendation -->"
EX_EMPLOYEES: frozenset[str] = frozenset(
    v.strip() for v in re.split(r"[\s,]+", os.environ.get("EX_EMPLOYEES", "")) if v.strip()
)
_PROMPT_PATH = Path(__file__).parent / "prompts" / "resolve_owner.txt"
_PR_NAME = re.compile(r"^- name:\s*[\"']?(.+?)[\"']?\s*$")
_PR_OWNER = re.compile(r"^\s+owner_id:\s*(.+)")


def load_pipeline_reorg_owners(reorg_dir: Path) -> list[dict[str, str]]:
    """Parse `- name: JOB` / `owner_id: SLACK # Real Name` pairs from tt-metal pipeline YAMLs."""
    out: list[dict[str, str]] = []
    if not reorg_dir.exists():
        log(f"  Warning: {reorg_dir} not found")
        return out
    for yf in sorted(reorg_dir.glob("*.yaml")):
        cur: str | None = None
        for line in yf.read_text().splitlines():
            if m := _PR_NAME.match(line):
                cur = m.group(1)
                continue
            if (m := _PR_OWNER.match(line)) and cur:
                rest = m.group(1).strip()
                sid = rest.split("#")[0].strip().split()[0]
                name = rest.split("#", 1)[1].strip() if "#" in rest else ""
                out.append({"name": cur, "id": sid, "owner_name": name})
                cur = None
    return out


_active_cache: dict[tuple[str, str], bool] = {}


def is_active_employee(slack_id: str, login: str, slack_dir: list[dict[str, Any]], token: str | None) -> bool:
    """Return False if ANY signal flags the person as gone:
      * explicit EX_EMPLOYEES override (Slack ID or GitHub login)
      * Slack user flagged `deleted: true`
      * Slack user completely absent from a non-empty dump (Slack admin removed them)

    We deliberately do NOT use GitHub org membership as a rejection signal. The
    `/orgs/{org}/members/{login}` endpoint returns 404 to any caller that lacks
    `read:org` scope for private memberships, which is what our AGGREGATE_READ_TOKEN
    typically looks like. A 404 under those conditions is indistinguishable from
    "truly not a member", so using it would reject real employees (sadesoyeTT,
    cglagovichTT, ...). EX_EMPLOYEES is the designated escape hatch for people
    whose Slack account is still live because HR has not offboarded them yet.
    """
    key = (slack_id, login)
    if key in _active_cache:
        return _active_cache[key]
    reason = ""
    if slack_id and slack_id in EX_EMPLOYEES:
        reason = "ex-employees override (slack_id)"
    elif login and login in EX_EMPLOYEES:
        reason = "ex-employees override (github login)"
    if not reason and slack_id:
        user = next((u for u in slack_dir if u.get("id") == slack_id), None)
        if slack_dir and user is None:
            reason = "not present in Slack workspace dump"
        elif user and user.get("deleted"):
            reason = "Slack account deactivated"
    active = not reason
    log(f"  active-check: slack_id={slack_id or '-'} login={login or '-'} -> "
        + ("active" if active else f"inactive ({reason})"))
    _active_cache[key] = active
    return active


def _resolve_via_agent(workflow_name: str, job_name: str, ex_owner_note: str,
                       slack_dir: list[dict[str, Any]] | None = None,
                       token: str | None = None) -> dict[str, Any]:
    prompt = string.Template(_PROMPT_PATH.read_text()).substitute(
        workflow_name=workflow_name, job_name=job_name,
        ex_owner_note=ex_owner_note or "(no previous owner recorded)",
        ex_employees=", ".join(sorted(EX_EMPLOYEES)) or "(none configured)",
        marker=AGENT_MARKER, repo_root=str(TARGET_REPO_ROOT),
        slack_dump=str(SLACK_DUMP_PATH), github_org=GITHUB_ORG,
    )
    empty = {"source": "none", "github_assignees": [], "github_names": [], "slack_assignees": [], "slack_names": []}
    cmd = ["agent", "--trust", "-p", prompt]
    if CURSOR_MODEL != "auto":
        cmd[1:1] = ["--model", CURSOR_MODEL]
    # Forward everything the agent needs to run `check_active` itself, including
    # EX_EMPLOYEES and SLACK_DUMP_PATH, so the employment check inside the agent
    # loop sees the same config as the outer resolver.
    env = {
        k: os.environ[k]
        for k in (
            "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL",
            "CURSOR_API_KEY", "GITHUB_TOKEN",
            "EX_EMPLOYEES", "SLACK_DUMP_PATH", "PYTHONPATH",
        )
        if k in os.environ
    }
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"Cursor agent exited {proc.returncode}: {proc.stderr[:200]}")
        text = proc.stdout or ""
        idx = text.rfind(AGENT_MARKER)
        if idx < 0:
            raise ValueError(f"Marker {AGENT_MARKER!r} not in agent output")
        payload = text[idx + len(AGENT_MARKER):].strip()
        if payload.startswith("```"):
            payload = payload.split("\n", 1)[-1]
            if payload.rstrip().endswith("```"):
                payload = payload.rstrip()[:-3]
        res = json.loads(payload.strip())
    except Exception as exc:  # noqa: BLE001
        log(f"  Agent resolution failed: {exc}")
        return empty
    gh_login = str(res.get("github_login") or "")
    sid = str(res.get("slack_id") or "")
    if not (gh_login or sid):
        return empty
    if not is_active_employee(sid, gh_login, slack_dir or [], token):
        log(f"  Agent picked an inactive candidate (slack={sid!r}, login={gh_login!r}); dropping.")
        return empty
    return {
        "source": "agent",
        "github_assignees": [gh_login] if gh_login else [],
        "github_names": [str(res.get("github_name") or "")] if gh_login else [],
        "slack_assignees": [sid] if sid else [],
        "slack_names": [str(res.get("slack_name") or "")] if sid else [],
    }


def resolve_owner(workflow_name: str, job_name: str, pipeline: list[dict[str, str]],
                  slack_dir: list[dict[str, Any]], token: str | None,
                  identity_index: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Fast path: pipeline_reorg entry. Slow path (no match or ex-employee): agent."""
    bare = job_name.rsplit(" / ", 1)[-1].strip()
    entry = next((e for e in pipeline if e["name"] in (job_name, bare)), None)
    if not entry:
        return _resolve_via_agent(workflow_name, job_name, "", slack_dir, token)
    sid = entry["id"]
    user = next((u for u in slack_dir if u.get("id") == sid), {})
    display = entry.get("owner_name") or user.get("real_name") or user.get("display_name") or sid
    ident = (identity_index or {}).get(sid, {})
    gh_login = ident.get("github_login", "")
    gh_name = ident.get("github_name", "")
    if is_active_employee(sid, gh_login, slack_dir, token):
        return {
            "source": "pipeline_reorg",
            "github_assignees": [gh_login] if gh_login else [],
            "github_names": [gh_name] if gh_login else [],
            "slack_assignees": [sid],
            "slack_names": [display if display != sid else ""],
        }
    return _resolve_via_agent(
        workflow_name, job_name,
        f"The previous owner {display} (Slack `{sid}`) has left the company. Find a current replacement.",
        slack_dir, token,
    )


def _display_name(r: dict[str, Any]) -> str:
    """Pick the human-readable name for the summary / comment. Never exposes Slack IDs or GH logins."""
    for key in ("github_names", "slack_names"):
        for value in r.get(key) or []:
            value = (value or "").strip()
            if value:
                return value
    return ""


def _render_comment(name: str) -> str:
    body = name or "_no current owner could be determined_"
    return f"{COMMENT_MARKER}\n**Recommended owner:** {body}\n"


def _find_marker_comment(comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((c for c in comments if COMMENT_MARKER in (c.get("body") or "")), None)


def render_summary(updated: list[dict[str, Any]], skipped: list[dict[str, Any]],
                   unchanged: list[dict[str, Any]], failed: list[dict[str, Any]]) -> str:
    L = ["# Owner Recommendation Summary", "",
         f"- **Updated:** {len(updated)}", f"- **Unchanged (idempotent):** {len(unchanged)}",
         f"- **Skipped (missing base metadata):** {len(skipped)}"]
    if failed:
        L.append(f"- **Failed:** {len(failed)}")
    L.append("")
    if updated:
        L += ["## Recommended owners", "", "| Issue | Recommended owner |", "| --- | --- |"]
        L += [f"| #{r['number']} | {_display_name(r) or '_unresolved_'} |" for r in updated]
        L.append("")
    if unchanged:
        L += ["## Already up-to-date", "",
              *[f"- #{r['number']}: {_display_name(r) or '_unresolved_'}" for r in unchanged], ""]
    for title, rows in (("## Skipped", skipped), ("## Failed (transient / unexpected)", failed)):
        if rows:
            L += [title, "", *[f"- #{r['number']}: {r['reason']}" for r in rows], ""]
    if not (updated or unchanged or skipped or failed):
        L += ["No CI auto-triage issues found.", ""]
    return "\n".join(L)


def main() -> int:
    log("=== Assign Owners (hybrid: pipeline_reorg + agent) ===")
    if not ISSUE_WRITE_TOKEN:
        log("ISSUE_WRITE_TOKEN is required."); return 1
    if not os.environ.get("CURSOR_API_KEY"):
        log("CURSOR_API_KEY is required for the agent fallback."); return 1
    issues = list_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)
    pipeline = load_pipeline_reorg_owners(PIPELINE_REORG_DIR)
    log(f"  Loaded {len(pipeline)} pipeline_reorg entries from {PIPELINE_REORG_DIR}")
    slack_dir: list[dict[str, Any]] = []
    if os.environ.get("SLACK_BOT_TOKEN"):
        try:
            slack_dir = download_slack_directory(os.environ["SLACK_BOT_TOKEN"])
            SLACK_DUMP_PATH.write_text(json.dumps(slack_dir), encoding="utf-8")
            log(f"  Slack directory: {len(slack_dir)} users (dumped to {SLACK_DUMP_PATH})")
        except Exception as exc:  # noqa: BLE001
            log(f"  Warning: failed to download Slack directory: {exc}")
    identity_index = build_identity_index(TARGET_REPO_ROOT, slack_dir) if slack_dir else {}
    token = AGGREGATE_READ_TOKEN or None
    updated: list[dict[str, Any]] = []; skipped: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []; failed: list[dict[str, Any]] = []

    for issue in issues:
        num = issue["number"]
        try:
            body = issue.get("body", "") or ""
            base = parse_base_markers(body)
            wf, job = str(base["workflow_name"]), str(base["job_name"])
            if not wf or not job:
                log(f"  #{num}: missing base metadata, skipping")
                skipped.append({"number": num, "reason": "missing Auto-triage-workflow / Auto-triage-job-name"})
                continue
            r = resolve_owner(wf, job, pipeline, slack_dir, token, identity_index)
            name = _display_name(r)
            log(f"  #{num}: source={r['source']} owner={name!r} gh={r['github_assignees']} slack={r['slack_assignees']}")

            # Clean out any stale assignee markers left behind by earlier runs of this stage.
            if has_assignee_markers(body):
                update_issue(ISSUE_REPO, num, strip_assignee_markers(body), ISSUE_WRITE_TOKEN, add_labels=[OWNERS_READY_LABEL])
                log(f"  #{num}: stripped stale assignee markers from issue body")
            elif OWNERS_READY_LABEL not in {lb.get("name", "") for lb in issue.get("labels", [])}:
                add_issue_labels(ISSUE_REPO, num, ISSUE_WRITE_TOKEN, [OWNERS_READY_LABEL])

            # Upsert the single marker-gated recommendation comment. Never pings anyone.
            want_comment = _render_comment(name)
            existing = _find_marker_comment(list_issue_comments(ISSUE_REPO, num, ISSUE_WRITE_TOKEN))
            if existing and (existing.get("body") or "").strip() == want_comment.strip():
                unchanged.append({"number": num, **r})
                continue
            if existing:
                update_issue_comment(ISSUE_REPO, existing["id"], want_comment, ISSUE_WRITE_TOKEN)
                log(f"  #{num}: updated owner-recommendation comment")
            else:
                create_issue_comment(ISSUE_REPO, num, want_comment, ISSUE_WRITE_TOKEN)
                log(f"  #{num}: created owner-recommendation comment")
            updated.append({"number": num, **r})
        except Exception as exc:  # noqa: BLE001
            log(f"  #{num}: failed — {exc}")
            failed.append({"number": num, "reason": str(exc)})

    summary = render_summary(updated, skipped, unchanged, failed)
    (Path(SUMMARY_OUTPUT).write_text(summary, encoding="utf-8") if SUMMARY_OUTPUT else print(summary))
    print(json.dumps({"updated": updated, "unchanged": unchanged, "skipped": skipped, "failed": failed}, indent=2), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

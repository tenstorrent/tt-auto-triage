#!/usr/bin/env python3
# Hybrid owner resolver: pipeline_reorg fast path, Cursor or Copilot agent slow path.
from __future__ import annotations

import json
import os
import re
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from .check_active import check as _check_employee_status
from .github import (
    add_issue_labels,
    create_issue_comment,
    download_slack_directory,
    get_issue,
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
CURSOR_MODEL = os.environ.get("CURSOR_MODEL", "auto")
LLM_BACKEND = os.environ.get("LLM_BACKEND", "cursor").strip().lower() or "cursor"
GITHUB_ORG = os.environ.get("GITHUB_ORG", "tenstorrent")
OWNERS_READY_LABEL = "auto-triage:owners-ready"
AGENT_MARKER = "===FINAL==="
COMMENT_MARKER = "<!-- auto-triage:owner-recommendation -->"
EX_EMPLOYEES: frozenset[str] = frozenset(
    v.strip() for v in re.split(r"[\s,]+", os.environ.get("EX_EMPLOYEES", "")) if v.strip()
)
# `true` / `1` / `yes` (case-insensitive) = skip issues that already have a named owner.
# Anything else (including unset) = process every issue as usual, which is the legacy behavior.
def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"true", "1", "yes"}


SKIP_ALREADY_ASSIGNED = _bool_env("SKIP_ALREADY_ASSIGNED")
# Re-resolve and EXCLUDE every prior recommendation for this issue from the
# search. Forces a different pick.
REASSIGN_TO_SOMEONE_ELSE = _bool_env("REASSIGN_TO_SOMEONE_ELSE")
# Re-resolve, but do NOT exclude prior owners. Useful when something upstream
# (e.g. a pipeline_reorg edit) probably changes the answer.
REFRESH_OWNER_RECOMMENDATION = _bool_env("REFRESH_OWNER_RECOMMENDATION")
# Free-form dev guidance appended to the agent prompt under an EXTRA INPUT FROM
# DEVS header. When non-empty, the deterministic pipeline_reorg fast path is
# bypassed for every issue this run touches and resolution always goes through
# the agent so the hint can actually influence the pick.
EXTRA_CONTEXT_FOR_AGENT = os.environ.get("EXTRA_CONTEXT_FOR_AGENT", "").strip()
_UNRESOLVED_OWNER_BODY = "_no current owner could be determined_"
_PROMPT_PATH = Path(__file__).parent / "prompts" / "resolve_owner.txt"
_PR_NAME = re.compile(r"^- name:\s*[\"']?(.+?)[\"']?\s*$")
_PR_OWNER = re.compile(r"^\s+owner_id:\s*(.+)")
_ISSUE_NUM_RE = re.compile(r"/issues/(\d+)|#?(\d+)")
_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)\n```\s*$", re.DOTALL)
# Sentinel pair around the EXTRA INPUT FROM DEVS block in resolve_owner.txt.
# Matched and stripped (with the bracketed content) when no hint was supplied,
# or just the sentinel lines themselves are dropped when a hint is present.
_EXTRA_BLOCK_RE = re.compile(
    r"^EXTRA_INPUT_BLOCK_BEGIN\n(.*?)^EXTRA_INPUT_BLOCK_END\n",
    re.DOTALL | re.MULTILINE,
)
_RECOMMENDED_RE = re.compile(r"^\*\*Recommended owner:\*\*\s*(.+?)\s*$", re.MULTILINE)
_PREVIOUS_RE = re.compile(r"^\*\*Previous owners:\*\*\s*(.+?)\s*$", re.MULTILINE)


def parse_issue_numbers(raw: str) -> list[int]:
    """Extract issue numbers from a free-form user input. Accepts bare
    numbers (`888`), hash-prefixed numbers (`#888`), and full GitHub issue
    URLs (`https://github.com/owner/repo/issues/888`), mixed and separated
    by commas and/or whitespace. Returns a deduplicated, order-preserving
    list of ints. Anything that doesn't contain a number is silently
    skipped so a stray blank token can't crash the run."""
    if not raw:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for tok in re.split(r"[\s,]+", raw.strip()):
        if not tok:
            continue
        m = _ISSUE_NUM_RE.search(tok)
        if not m:
            continue
        num = int(m.group(1) or m.group(2))
        if num not in seen:
            seen.add(num)
            out.append(num)
    return out


def load_pipeline_reorg_owners(reorg_dir: Path) -> dict[str, dict[str, str]]:
    """Parse `- name: JOB` / `owner_id: SLACK # Real Name` pairs from tt-metal pipeline YAMLs.
    Returns a dict keyed by job name for O(1) lookup."""
    out: dict[str, dict[str, str]] = {}
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
                out[cur] = {"name": cur, "id": sid, "owner_name": name}
                cur = None
    return out


_active_cache: dict[tuple[str, str, frozenset[str]], bool] = {}


def is_active_employee(slack_id: str, login: str, slack_dir: list[dict[str, Any]],
                       extra_ex: frozenset[str] = frozenset()) -> bool:
    """Return False if ANY signal flags the person as gone:
      * explicit EX_EMPLOYEES override (Slack ID or GitHub login)
      * per-issue `extra_ex` blacklist (used by reassign-to-someone-else)
      * Slack user flagged `deleted: true`
      * Slack user completely absent from a non-empty dump (Slack admin removed them)

    We deliberately do NOT use GitHub org membership as a rejection signal. The
    `/orgs/{org}/members/{login}` endpoint returns 404 to any caller that lacks
    `read:org` scope for private memberships, which is what our AGGREGATE_READ_TOKEN
    typically looks like. A 404 under those conditions is indistinguishable from
    "truly not a member", so using it would reject real employees (sadesoyeTT,
    cglagovichTT, ...). EX_EMPLOYEES is the designated escape hatch for people
    whose Slack account is still live because HR has not offboarded them yet.

    `extra_ex` is included in the cache key so that the same person can be
    ACTIVE in one issue's resolution (no per-issue blacklist) and INACTIVE in
    another (where they're the previous recommendation we want to replace).
    """
    key = (slack_id, login, extra_ex)
    if key in _active_cache:
        return _active_cache[key]
    merged = EX_EMPLOYEES | extra_ex
    active, reason = _check_employee_status(slack_id, login, slack_dir, merged)
    log(f"  active-check: slack_id={slack_id or '-'} login={login or '-'} -> "
        + ("active" if active else f"inactive ({reason})"))
    _active_cache[key] = active
    return active


def _build_prompt(workflow_name: str, job_name: str, ex_owner_note: str,
                  ex_employees_display: str, extra_context: str) -> str:
    """Substitute prompt vars and either drop or unwrap the EXTRA INPUT FROM DEVS
    block depending on whether a dev hint was supplied. Sentinel lines around
    the block are always removed; the block contents are only kept when there's
    actually something to show the agent."""
    template = _PROMPT_PATH.read_text()
    if extra_context:
        # Keep the inner content, drop just the sentinel lines.
        template = _EXTRA_BLOCK_RE.sub(lambda m: m.group(1), template)
    else:
        # Drop the entire block (sentinels and contents).
        template = _EXTRA_BLOCK_RE.sub("", template)
    return string.Template(template).substitute(
        workflow_name=workflow_name, job_name=job_name,
        ex_owner_note=ex_owner_note or "(no previous owner recorded)",
        ex_employees=ex_employees_display or "(none configured)",
        extra_context=extra_context or "(no extra input provided)",
        marker=AGENT_MARKER, repo_root=str(TARGET_REPO_ROOT),
        slack_dump=str(SLACK_DUMP_PATH), github_org=GITHUB_ORG,
    )


# Env vars forwarded to BOTH Cursor and Copilot agents. Includes everything the
# `check_active` subprocess needs (PYTHONPATH for the module, SLACK_DUMP_PATH
# for the dump location, GITHUB_TOKEN for any gh API calls the agent makes).
_AGENT_BASE_ENV_KEYS: tuple[str, ...] = (
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL",
    "GITHUB_TOKEN", "SLACK_DUMP_PATH", "PYTHONPATH",
)
_AGENT_TIMEOUT_SECONDS = 900


def _agent_env(secret_keys: tuple[str, ...], merged_ex: frozenset[str]) -> dict[str, str]:
    """Build the env dict forwarded to the agent subprocess. EX_EMPLOYEES is
    OVERRIDDEN with the merged (global + per-issue) blacklist so the agent's
    own check_active calls reject every blacklisted candidate without needing
    a separate per-issue CLI arg."""
    env = {
        k: os.environ[k]
        for k in _AGENT_BASE_ENV_KEYS + secret_keys
        if k in os.environ
    }
    if merged_ex:
        env["EX_EMPLOYEES"] = ",".join(sorted(merged_ex))
    return env


def _run_cursor_agent(prompt: str, merged_ex: frozenset[str]) -> str:
    cmd = ["agent", "--trust", "-p", prompt]
    if CURSOR_MODEL and CURSOR_MODEL != "auto":
        cmd[1:1] = ["--model", CURSOR_MODEL]
    env = _agent_env(("CURSOR_API_KEY",), merged_ex)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_AGENT_TIMEOUT_SECONDS, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"Cursor agent exited {proc.returncode}: {proc.stderr[:200]}")
    return proc.stdout or ""


def _run_copilot_agent(prompt: str, merged_ex: frozenset[str]) -> str:
    cmd = ["copilot", "-p", prompt, "--allow-all-tools"]
    env = _agent_env(("COPILOT_GITHUB_TOKEN",), merged_ex)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_AGENT_TIMEOUT_SECONDS, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"Copilot agent exited {proc.returncode}: {proc.stderr[:200]}")
    return proc.stdout or ""


def _run_llm_agent(prompt: str, merged_ex: frozenset[str]) -> str:
    if LLM_BACKEND == "copilot":
        return _run_copilot_agent(prompt, merged_ex)
    return _run_cursor_agent(prompt, merged_ex)


def _resolve_via_agent(workflow_name: str, job_name: str, ex_owner_note: str,
                       slack_dir: list[dict[str, Any]] | None = None,
                       extra_ex: frozenset[str] = frozenset(),
                       extra_context: str = "") -> dict[str, Any]:
    merged_ex = EX_EMPLOYEES | extra_ex
    ex_display = ", ".join(sorted(merged_ex))
    prompt = _build_prompt(workflow_name, job_name, ex_owner_note, ex_display, extra_context)
    empty = {"source": "none", "github_assignees": [], "github_names": [], "slack_assignees": [], "slack_names": []}
    try:
        text = _run_llm_agent(prompt, merged_ex)
        idx = text.rfind(AGENT_MARKER)
        if idx < 0:
            raise ValueError(f"Marker {AGENT_MARKER!r} not in agent output")
        payload = text[idx + len(AGENT_MARKER):].strip()
        m = _FENCE_RE.match(payload)
        if m:
            payload = m.group(1)
        res = json.loads(payload.strip())
    except Exception as exc:  # noqa: BLE001
        log(f"  Agent resolution failed: {exc}")
        return empty
    gh_login = str(res.get("github_login") or "")
    sid = str(res.get("slack_id") or "")
    if not (gh_login or sid):
        return empty
    if not is_active_employee(sid, gh_login, slack_dir or [], extra_ex):
        log(f"  Agent picked an inactive candidate (slack={sid!r}, login={gh_login!r}); dropping.")
        return empty
    return {
        "source": "agent",
        "github_assignees": [gh_login] if gh_login else [],
        "github_names": [str(res.get("github_name") or "")] if gh_login else [],
        "slack_assignees": [sid] if sid else [],
        "slack_names": [str(res.get("slack_name") or "")] if sid else [],
    }


def resolve_owner(workflow_name: str, job_name: str, pipeline: dict[str, dict[str, str]],
                  slack_dir: list[dict[str, Any]],
                  identity_index: dict[str, dict[str, str]] | None = None,
                  extra_ex: frozenset[str] = frozenset(),
                  extra_context: str = "",
                  skip_fast_path: bool = False) -> dict[str, Any]:
    """Fast path: pipeline_reorg entry. Slow path (no match, ex-employee, or
    `skip_fast_path=True` because the caller has dev hint context that should
    influence the pick): agent.

    `extra_ex` is a per-issue blacklist (Slack IDs and/or GitHub logins) that
    augments EX_EMPLOYEES for this single resolution. `extra_context` is dev-
    supplied free-form text that flows into the agent prompt under an EXTRA
    INPUT FROM DEVS header. `skip_fast_path` forces the agent path even if a
    deterministic match exists; used whenever `extra_context` is non-empty.
    """
    if skip_fast_path:
        return _resolve_via_agent(workflow_name, job_name, "", slack_dir, extra_ex, extra_context)
    bare = job_name.rsplit(" / ", 1)[-1].strip()
    entry = pipeline.get(job_name) or pipeline.get(bare)
    if not entry:
        return _resolve_via_agent(workflow_name, job_name, "", slack_dir, extra_ex, extra_context)
    sid = entry["id"]
    user = next((u for u in slack_dir if u.get("id") == sid), {})
    display = entry.get("owner_name") or user.get("real_name") or user.get("display_name") or sid
    ident = (identity_index or {}).get(sid, {})
    gh_login = ident.get("github_login", "")
    gh_name = ident.get("github_name", "")
    if is_active_employee(sid, gh_login, slack_dir, extra_ex):
        return {
            "source": "pipeline_reorg",
            "github_assignees": [gh_login] if gh_login else [],
            "github_names": [gh_name] if gh_login else [],
            "slack_assignees": [sid],
            "slack_names": [display if display != sid else ""],
        }
    return _resolve_via_agent(
        workflow_name, job_name,
        f"The previous owner {display} (Slack `{sid}`) has left the company or is excluded for this run. Find a current replacement.",
        slack_dir, extra_ex, extra_context,
    )


def _display_name(r: dict[str, Any]) -> str:
    """Pick the human-readable name for the summary / comment. Never exposes Slack IDs or GH logins."""
    for key in ("github_names", "slack_names"):
        for value in r.get(key) or []:
            value = (value or "").strip()
            if value:
                return value
    return ""


def _render_comment(name: str, previous: list[str] | None = None) -> str:
    """Render the marker-gated recommendation comment. With no `previous`, this
    matches the legacy single-line format; with one or more, prepends a
    `**Previous owners:**` line that grows over re-resolutions.

    The body deliberately stays plain text — first/last names, no GitHub logins,
    no Slack IDs, no @ pings. We rely on the unique-first-and-last-name
    assumption to round-trip the names back through reverse lookup later.
    """
    body = name or _UNRESOLVED_OWNER_BODY
    lines = [COMMENT_MARKER]
    if previous:
        lines.append(f"**Previous owners:** {', '.join(previous)}")
    lines.append(f"**Recommended owner:** {body}")
    return "\n".join(lines) + "\n"


def _parse_recommendation_comment(body: str) -> tuple[list[str], str]:
    """Inverse of `_render_comment`. Returns `(previous_owners, current_owner)`.

    Pure regex; no external deps. Handles legacy single-line bodies (no
    Previous line), full growing-history bodies, the unresolved-placeholder
    body, and stray whitespace. Returns `("", "")` for anything we can't parse.
    """
    if not body:
        return [], ""
    cur_match = _RECOMMENDED_RE.search(body)
    current = (cur_match.group(1).strip() if cur_match else "")
    if current == _UNRESOLVED_OWNER_BODY:
        current = ""
    prev_match = _PREVIOUS_RE.search(body)
    previous: list[str] = []
    if prev_match:
        previous = [p.strip() for p in prev_match.group(1).split(",") if p.strip()]
    return previous, current


def _identifiers_for_name(name: str, slack_dir: list[dict[str, Any]],
                          identity_index: dict[str, dict[str, str]] | None) -> tuple[str, str]:
    """Reverse-lookup a real-name string into `(slack_id, github_login)`.

    Matches case-insensitively on `real_name` first, then `display_name` as a
    fallback inside the Slack directory; matches `github_name` inside the
    identity index. First hit wins (we accept the unique-first-and-last-name
    assumption — see _render_comment). Returns `("", "")` and logs a warning if
    we couldn't resolve the name; the caller then gets a comment update without
    a corresponding blacklist entry, which is an acceptable failure mode.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return "", ""
    sid = ""
    for u in slack_dir or []:
        profile = u.get("profile") or {}
        candidates = (
            (u.get("real_name") or "").strip().lower(),
            (profile.get("real_name") or "").strip().lower(),
            (profile.get("display_name") or "").strip().lower(),
            (u.get("name") or "").strip().lower(),
        )
        if needle in candidates and any(candidates):
            sid = u.get("id") or ""
            break
    login = ""
    for ident in (identity_index or {}).values():
        if (ident.get("github_name") or "").strip().lower() == needle:
            login = ident.get("github_login") or ""
            break
    if not (sid or login):
        log(f"  Could not reverse-lookup '{name}' for blacklist; leaving them out")
    return sid, login


def _find_marker_comment(comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((c for c in comments if COMMENT_MARKER in (c.get("body") or "")), None)


def _has_named_owner_comment(comments: list[dict[str, Any]]) -> bool:
    """A recommendation comment "names an owner" if it carries our marker AND its
    body is not the unresolved-owner placeholder. This is the signal used by
    SKIP_ALREADY_ASSIGNED — an issue whose prior run failed to resolve (body is
    the placeholder) is still fair game for re-resolution on the next pass."""
    existing = _find_marker_comment(comments)
    if not existing:
        return False
    return _UNRESOLVED_OWNER_BODY not in (existing.get("body") or "")


def render_summary(updated: list[dict[str, Any]], skipped: list[dict[str, Any]],
                   unchanged: list[dict[str, Any]], failed: list[dict[str, Any]],
                   already_assigned: list[dict[str, Any]] | None = None) -> str:
    already_assigned = already_assigned or []
    L = ["# Owner Recommendation Summary", "",
         f"- **Updated:** {len(updated)}", f"- **Unchanged (idempotent):** {len(unchanged)}",
         f"- **Skipped (already assigned):** {len(already_assigned)}",
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
    if already_assigned:
        L += ["## Skipped because an owner was already recommended", "",
              *[f"- #{r['number']}" for r in already_assigned], ""]
    for title, rows in (("## Skipped", skipped), ("## Failed (transient / unexpected)", failed)):
        if rows:
            L += [title, "", *[f"- #{r['number']}: {r['reason']}" for r in rows], ""]
    if not (updated or unchanged or skipped or failed or already_assigned):
        L += ["No CI auto-triage issues found.", ""]
    return "\n".join(L)


def _validate_flags() -> str:
    """Return an error message if the flag combination is illegal, else ''.
    Forces every caller to state their intent up-front rather than silently
    no-op'ing or silently doing the wrong thing."""
    extras_set = REASSIGN_TO_SOMEONE_ELSE or REFRESH_OWNER_RECOMMENDATION or bool(EXTRA_CONTEXT_FOR_AGENT)
    if SKIP_ALREADY_ASSIGNED and extras_set:
        return ("skip-assigning-for-issues-that-already-have-owners=true is incompatible with "
                "reassign-to-someone-else / refresh-owner-recommendation / extra-context-for-agent; "
                "set skip=false to use them.")
    if REASSIGN_TO_SOMEONE_ELSE and REFRESH_OWNER_RECOMMENDATION:
        return "reassign-to-someone-else and refresh-owner-recommendation are mutually exclusive; pick one."
    if not SKIP_ALREADY_ASSIGNED and not extras_set:
        return ("skip=false requires at least one of reassign-to-someone-else, refresh-owner-recommendation, "
                "or extra-context-for-agent. Otherwise set skip=true (the default).")
    return ""


def main() -> int:
    log("=== Assign Owners (hybrid: pipeline_reorg + agent) ===")
    if not ISSUE_WRITE_TOKEN:
        log("ISSUE_WRITE_TOKEN is required.")
        return 1
    if LLM_BACKEND not in ("cursor", "copilot"):
        log(f"LLM_BACKEND must be 'cursor' or 'copilot', got: {LLM_BACKEND!r}")
        return 1
    if LLM_BACKEND == "copilot" and not os.environ.get("COPILOT_GITHUB_TOKEN"):
        log("COPILOT_GITHUB_TOKEN is required when LLM_BACKEND=copilot.")
        return 1
    if LLM_BACKEND == "cursor" and not os.environ.get("CURSOR_API_KEY"):
        log("CURSOR_API_KEY is required when LLM_BACKEND=cursor.")
        return 1
    err = _validate_flags()
    if err:
        log(f"Invalid input combination: {err}")
        return 1
    skip_fast_path = bool(EXTRA_CONTEXT_FOR_AGENT)
    log(f"  Flags: skip={SKIP_ALREADY_ASSIGNED} reassign={REASSIGN_TO_SOMEONE_ELSE} "
        f"refresh={REFRESH_OWNER_RECOMMENDATION} extra_context={'set' if EXTRA_CONTEXT_FOR_AGENT else 'unset'} "
        f"(fast-path bypassed: {skip_fast_path})")
    scoped = parse_issue_numbers(os.environ.get("ISSUE_NUMBERS", ""))
    if scoped:
        log(f"  Scoped run: only processing issues {scoped} in {ISSUE_REPO}")
        issues = []
        for num in scoped:
            try:
                issues.append(get_issue(ISSUE_REPO, num, ISSUE_WRITE_TOKEN))
            except Exception as exc:  # noqa: BLE001
                log(f"  Warning: could not fetch #{num} from {ISSUE_REPO}: {exc}")
    else:
        issues = list_open_issues(ISSUE_REPO, ISSUE_WRITE_TOKEN)

    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    already_assigned: list[dict[str, Any]] = []

    # Early skip: drop any issue that already has a named-owner recommendation
    # comment BEFORE we pay for pipeline_reorg load, Slack dump, identity index,
    # or — most importantly — any agent call. Done as a pre-pass so that when
    # every targeted issue is already assigned, the workflow finishes in seconds
    # and never touches Slack / the agent at all. Honors both the scoped
    # (user picked specific issue numbers) and unscoped (every open issue) paths.
    if SKIP_ALREADY_ASSIGNED:
        pending: list[dict[str, Any]] = []
        for issue in issues:
            num = issue["number"]
            try:
                if _has_named_owner_comment(list_issue_comments(ISSUE_REPO, num, ISSUE_WRITE_TOKEN)):
                    log(f"  #{num}: already has a recommended-owner comment, skipping (SKIP_ALREADY_ASSIGNED=true)")
                    already_assigned.append({"number": num})
                    continue
            except Exception as exc:  # noqa: BLE001
                # If we can't even read the comments, don't silently process the
                # issue as if unassigned — record it and move on. The main loop
                # below will not re-attempt it because it's not in `pending`.
                log(f"  #{num}: failed to read comments for skip-check — {exc}")
                failed.append({"number": num, "reason": f"comment read failed: {exc}"})
                continue
            pending.append(issue)
        issues = pending

    # Short-circuit: if every issue was already assigned (or there were no issues
    # in the first place), we skip the expensive setup entirely and emit an empty
    # summary. This is the "if all issues already have owners, the workflow should
    # be super fast" requirement.
    if not issues:
        summary = render_summary(updated, skipped, unchanged, failed, already_assigned)
        (Path(SUMMARY_OUTPUT).write_text(summary, encoding="utf-8") if SUMMARY_OUTPUT else print(summary))
        print(json.dumps({
            "updated": updated, "unchanged": unchanged, "skipped": skipped,
            "failed": failed, "already_assigned": already_assigned,
        }, indent=2), file=sys.stderr)
        return 1 if failed else 0

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

            # Parse any prior recommendation comment so we can both build the
            # per-issue blacklist (reassign mode) and grow the Previous owners
            # history. When SKIP_ALREADY_ASSIGNED is true the pre-pass above
            # already removed assigned issues from `issues`, so anything still
            # in this loop with a prior comment is here because the user opted
            # into reassign / refresh / extra-context.
            existing_comments = list_issue_comments(ISSUE_REPO, num, ISSUE_WRITE_TOKEN)
            existing = _find_marker_comment(existing_comments)
            prev_history, prev_current = _parse_recommendation_comment(existing.get("body") or "" if existing else "")

            extra_ex: frozenset[str] = frozenset()
            if REASSIGN_TO_SOMEONE_ELSE:
                blacklist_names = [n for n in (prev_history + ([prev_current] if prev_current else [])) if n]
                ids: set[str] = set()
                for nm in blacklist_names:
                    sid_b, login_b = _identifiers_for_name(nm, slack_dir, identity_index)
                    if sid_b:
                        ids.add(sid_b)
                    if login_b:
                        ids.add(login_b)
                extra_ex = frozenset(ids)
                if blacklist_names:
                    log(f"  #{num}: reassign blacklist names={blacklist_names} -> ids={sorted(extra_ex)}")

            r = resolve_owner(wf, job, pipeline, slack_dir, identity_index,
                              extra_ex=extra_ex, extra_context=EXTRA_CONTEXT_FOR_AGENT,
                              skip_fast_path=skip_fast_path)
            name = _display_name(r)
            log(f"  #{num}: source={r['source']} owner={name!r} gh={r['github_assignees']} slack={r['slack_assignees']}")

            # Clean out any stale assignee markers left behind by earlier runs of this stage.
            if has_assignee_markers(body):
                update_issue(ISSUE_REPO, num, strip_assignee_markers(body), ISSUE_WRITE_TOKEN, add_labels=[OWNERS_READY_LABEL])
                log(f"  #{num}: stripped stale assignee markers from issue body")
            elif OWNERS_READY_LABEL not in {lb.get("name", "") for lb in issue.get("labels", [])}:
                add_issue_labels(ISSUE_REPO, num, ISSUE_WRITE_TOKEN, [OWNERS_READY_LABEL])

            # Build the new growing-history previous list. Move the OLD
            # recommendation into the previous list iff it's a real name AND
            # it's different from the new pick (avoid `Previous: Alice ->
            # Recommended: Alice`, which is just noise on a refresh that
            # converged on the same answer).
            new_previous = list(prev_history)
            if prev_current and prev_current != name:
                if prev_current not in new_previous:
                    new_previous.append(prev_current)

            # Upsert the single marker-gated recommendation comment. Never pings anyone.
            want_comment = _render_comment(name, new_previous)
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

    summary = render_summary(updated, skipped, unchanged, failed, already_assigned)
    (Path(SUMMARY_OUTPUT).write_text(summary, encoding="utf-8") if SUMMARY_OUTPUT else print(summary))
    print(json.dumps({
        "updated": updated, "unchanged": unchanged, "skipped": skipped,
        "failed": failed, "already_assigned": already_assigned,
    }, indent=2), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
#
# fetch_job_owner.py - Parse Slack thread text for job owner @mentions and resolve Slack IDs
#
# Usage:
#   JOB_NAME=... JOB_OWNER_FILE=... THREAD_TEXT_FILE=... SLACK_DATA_DIR=... python3 fetch_job_owner.py
#
# Reads thread text from THREAD_TEXT_FILE, finds @mentions on lines matching JOB_NAME,
# looks up Slack IDs from slack_directory.json and slack_groups.json in SLACK_DATA_DIR,
# writes JSON array of {name, slack_id} to JOB_OWNER_FILE.
#

import json
import os
import re
import sys


def add_owner(owners, seen, name, slack_id):
    key = f"{(slack_id or '').upper()}::{(name or '').strip().lower()}"
    if key in seen:
        return
    seen.add(key)
    owners.append({"name": (name or "").strip(), "slack_id": (slack_id or "").strip()})


def main():
    job_name = os.environ.get("JOB_NAME", "")
    owner_file = os.environ.get("JOB_OWNER_FILE", "")
    thread_file = os.environ.get("THREAD_TEXT_FILE", "/tmp/thread_text.txt")
    slack_data_dir = os.environ.get("SLACK_DATA_DIR", ".auto_triage/auto_triage/data")

    if not job_name or not owner_file:
        print("JOB_NAME and JOB_OWNER_FILE are required", file=sys.stderr)
        sys.exit(1)

    owners_result = []
    seen_owners = set()

    try:
        with open(thread_file) as f:
            text = f.read()
    except OSError as e:
        print(f"Cannot read thread text: {e}", file=sys.stderr)
        sys.exit(1)

    for line in text.split("\n"):
        # Strip Slack link formatting for matching: <url|label> -> label
        clean = re.sub(r"<[^>]+\|([^>]+)>", r"\1", line)
        clean = re.sub(r"<[^>]+>", "", clean)

        if job_name.lower() not in clean.lower():
            continue

        if os.environ.get("FETCH_JOB_OWNER_DEBUG"):
            print(f"Matched line: {clean[:200]}")

        # Slack rich_text mentions may appear as explicit mention tokens.
        # Parse these from the raw line before angle-bracket cleanup strips them.
        # Both forms are accepted: <@U12345> and <@U12345|fallback_label>. Slack
        # mrkdwn text.text payloads sometimes deliver the pipe form even though
        # rich_text blocks emit the bare form.
        for uid in re.findall(r"<@([A-Z0-9]+)(?:\|[^>]+)?>", line):
            add_owner(owners_result, seen_owners, "", uid)

        for sid in re.findall(r"<!subteam\^([A-Z0-9]+)(?:\|[^>]+)?>", line):
            add_owner(owners_result, seen_owners, "", sid)

        # Owner names follow the job description, prefixed with @
        # Names are letter-based (no leading digits) to avoid capturing
        # trailing text like "27 other pipelines are failing"
        found = re.findall(r"@([A-Za-z][A-Za-z .\-]*[A-Za-z])", clean)
        for name in found:
            name = re.sub(r"\s{2,}", " ", name).strip()
            if name.lower() in job_name.lower():
                continue
            add_owner(owners_result, seen_owners, name, "")

        if owners_result:
            break

    # Look up Slack IDs with exact case-insensitive matching
    slack_dir = os.path.join(slack_data_dir, "slack_directory.json")
    slack_groups = os.path.join(slack_data_dir, "slack_groups.json")
    users_data = {}
    if os.path.exists(slack_dir):
        try:
            with open(slack_dir) as f:
                users_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            if os.environ.get("FETCH_JOB_OWNER_DEBUG"):
                print(f"Warning: could not load Slack directory from {slack_dir}: {e}", file=sys.stderr)
            users_data = {}
    groups_data = {}
    if os.path.exists(slack_groups):
        try:
            with open(slack_groups) as f:
                groups_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            if os.environ.get("FETCH_JOB_OWNER_DEBUG"):
                print(f"Warning: could not load Slack groups from {slack_groups}: {e}", file=sys.stderr)
            groups_data = {}
    users = users_data.get("users", []) if isinstance(users_data, dict) else users_data
    groups = groups_data.get("usergroups", []) if isinstance(groups_data, dict) else groups_data
    users_by_id = {u.get("id", ""): u for u in users if isinstance(u, dict)}
    groups_by_id = {g.get("id", ""): g for g in groups if isinstance(g, dict)}

    for owner in owners_result:
        sid = owner.get("slack_id", "")

        # If mention token already provided an ID, backfill a readable name.
        # Fall back to the raw ID so the entry is never lost: directory data
        # may be missing (offline/test runs) or the ID may not yet be cached
        # (newly added users/groups, or W-prefixed Enterprise Grid users).
        if sid:
            resolved_name = (owner.get("name") or "").strip()
            if sid.startswith(("U", "W")):
                u = users_by_id.get(sid)
                if isinstance(u, dict):
                    resolved_name = (
                        resolved_name
                        or u.get("display_name")
                        or u.get("real_name")
                        or u.get("username")
                        or ""
                    )
            elif sid.startswith("S"):
                g = groups_by_id.get(sid)
                if isinstance(g, dict):
                    resolved_name = resolved_name or g.get("name") or g.get("handle") or ""
            owner["name"] = resolved_name or sid
            continue

        nl = owner.get("name", "").lower()
        if not nl:
            continue

        for u in users:
            if not isinstance(u, dict) or u.get("deleted") or u.get("is_bot"):
                continue
            if any((u.get(f) or "").lower() == nl for f in ("real_name", "display_name", "username")):
                owner["slack_id"] = u.get("id", "")
                break
        if not owner.get("slack_id"):
            for g in groups:
                if not isinstance(g, dict):
                    continue
                if any((g.get(f) or "").lower() == nl for f in ("name", "handle")):
                    owner["slack_id"] = g.get("id", "")
                    break

    # Final de-duplication pass after Slack ID resolution: if the same person was
    # captured both as @Name and as <@U...>, keep one enriched entry.
    deduped = []
    dedupe_index = {}
    for owner in owners_result:
        sid = (owner.get("slack_id") or "").strip()
        name = (owner.get("name") or "").strip()
        key = sid if sid else f"name::{name.lower()}"
        existing_idx = dedupe_index.get(key)
        if existing_idx is None:
            dedupe_index[key] = len(deduped)
            deduped.append({"name": name, "slack_id": sid})
        else:
            if not deduped[existing_idx]["name"] and name:
                deduped[existing_idx]["name"] = name
    owners_result = deduped

    # Detect default metalinfra owner: when the metalinfra team is assigned as
    # fallback (no explicit owner for the job), mark the entry so the Slack
    # formatter can append a disclaimer.  The known metalinfra group ID in
    # tt-metal is S0985AN7TC5; we also match by name pattern for resilience.
    _METALINFRA_IDS = {"S0985AN7TC5"}
    _METALINFRA_NAME_PATTERNS = {"metal infra", "metalinfra", "metal infra team"}
    for owner in owners_result:
        sid = (owner.get("slack_id") or "").strip()
        name_lower = (owner.get("name") or "").strip().lower()
        if sid in _METALINFRA_IDS or name_lower in _METALINFRA_NAME_PATTERNS:
            owner["is_default_owner"] = True

    owner_dir = os.path.dirname(owner_file)
    if owner_dir:
        os.makedirs(owner_dir, exist_ok=True)
    with open(owner_file, "w") as f:
        json.dump(owners_result, f, indent=2)

    if os.environ.get("FETCH_JOB_OWNER_DEBUG"):
        for o in owners_result:
            print(f"Owner: {o['name']}, Slack ID: {o['slack_id'] or 'not found'}")
        print(f"Total: {len(owners_result)} owner(s) extracted")


if __name__ == "__main__":
    main()

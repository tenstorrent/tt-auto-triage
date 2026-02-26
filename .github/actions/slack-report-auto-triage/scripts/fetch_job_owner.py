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


def main():
    job_name = os.environ.get("JOB_NAME", "")
    owner_file = os.environ.get("JOB_OWNER_FILE", "")
    thread_file = os.environ.get("THREAD_TEXT_FILE", "/tmp/thread_text.txt")
    slack_data_dir = os.environ.get("SLACK_DATA_DIR", ".auto_triage/auto_triage/data")

    if not job_name or not owner_file:
        print("JOB_NAME and JOB_OWNER_FILE are required", file=sys.stderr)
        sys.exit(1)

    owners_result = []

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

        # Owner names follow the job description, prefixed with @
        # Names are letter-based (no leading digits) to avoid capturing
        # trailing text like "27 other pipelines are failing"
        found = re.findall(r"@([A-Za-z][A-Za-z .\-]*[A-Za-z])", clean)
        for name in found:
            name = re.sub(r"\s{2,}", " ", name).strip()
            if name.lower() in job_name.lower():
                continue
            owners_result.append({"name": name, "slack_id": ""})

        if owners_result:
            break

    # Look up Slack IDs with exact case-insensitive matching
    slack_dir = os.path.join(slack_data_dir, "slack_directory.json")
    slack_groups = os.path.join(slack_data_dir, "slack_groups.json")
    users_data = json.load(open(slack_dir)) if os.path.exists(slack_dir) else {}
    groups_data = json.load(open(slack_groups)) if os.path.exists(slack_groups) else {}
    users = users_data.get("users", []) if isinstance(users_data, dict) else users_data
    groups = groups_data.get("usergroups", []) if isinstance(groups_data, dict) else groups_data

    for owner in owners_result:
        nl = owner["name"].lower()
        for u in users:
            if not isinstance(u, dict) or u.get("deleted") or u.get("is_bot"):
                continue
            if any((u.get(f) or "").lower() == nl for f in ("real_name", "display_name", "username")):
                owner["slack_id"] = u.get("id", "")
                break
        if not owner["slack_id"]:
            for g in groups:
                if not isinstance(g, dict):
                    continue
                if any((g.get(f) or "").lower() == nl for f in ("name", "handle")):
                    owner["slack_id"] = g.get("id", "")
                    break

    with open(owner_file, "w") as f:
        json.dump(owners_result, f, indent=2)

    if os.environ.get("FETCH_JOB_OWNER_DEBUG"):
        for o in owners_result:
            print(f"Owner: {o['name']}, Slack ID: {o['slack_id'] or 'not found'}")
        print(f"Total: {len(owners_result)} owner(s) extracted")


if __name__ == "__main__":
    main()
